{-# LANGUAGE DataKinds #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}

module LocalTxSubmissionRawSpec (spec) where

import Codec.CBOR.Decoding qualified as CBOR
import Codec.CBOR.Encoding qualified as CBOR
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Control.Concurrent (threadDelay)
import Control.Concurrent.Async (async, cancel, wait)
import Control.Concurrent.MVar (newEmptyMVar, putMVar, takeMVar)
import Control.Exception (bracket, finally)
import Control.Exception qualified as Exception
import Control.Tracer (nullTracer)
import Crypto.Hash qualified as Crypto
import Data.ByteString qualified as BS
import Data.ByteString.Char8 qualified as BSC
import Data.ByteString.Lazy qualified as LBS
import Data.IORef (IORef, modifyIORef', newIORef, readIORef)
import Data.List (isInfixOf, isPrefixOf)
import Data.Text qualified as Text
import Data.Word (Word32)
import DwarfAdversary.LocalTxSubmission.Raw
import Network.Mux.Channel (Channel (..))
import Ouroboros.Network.ErrorPolicy (nullErrorPolicies)
import Ouroboros.Network.IOManager (withIOManager)
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Mux
    ( MiniProtocol (..)
    , MiniProtocolCb (..)
    , MiniProtocolLimits (..)
    , MiniProtocolNum (..)
    , OuroborosApplication (..)
    , RunMiniProtocol (..)
    , StartOnDemandOrEagerly (..)
    , mkMiniProtocolCbFromPeer
    )
import Ouroboros.Network.NodeToClient
    ( NodeToClientVersion
    , NodeToClientVersionData (..)
    , nodeToClientCodecCBORTerm
    , nodeToClientHandshakeCodec
    )
import Ouroboros.Network.Handshake
    ( HandshakeCallbacks (..)
    )
import Ouroboros.Network.Protocol.Handshake.Codec
    ( cborTermVersionDataCodec
    , noTimeLimitsHandshake
    )
import Ouroboros.Network.Protocol.Handshake.Version
    ( Acceptable (acceptableVersion)
    , Queryable (queryVersion)
    , foldMapVersions
    , simpleSingletonVersions
    )
import Ouroboros.Network.Protocol.LocalTxSubmission.Codec
    ( codecLocalTxSubmission
    )
import Ouroboros.Network.Protocol.LocalTxSubmission.Server
    ( LocalTxSubmissionServer (..)
    , localTxSubmissionServerPeer
    )
import Ouroboros.Network.Protocol.LocalTxSubmission.Type
    ( SubmitResult (..)
    )
import Ouroboros.Network.Server.RateLimiting
    ( AcceptedConnectionsLimit (..)
    )
import Ouroboros.Network.Snocket
    ( localAddressFromPath
    , localSnocket
    , makeLocalBearer
    )
import Ouroboros.Network.Socket
    ( newNetworkMutableState
    , nullNetworkServerTracers
    , SomeResponderApplication (..)
    , withServerNode
    )
import Ouroboros.Network.Util.ShowProxy (ShowProxy)
import System.Directory (getTemporaryDirectory, removeFile)
import System.IO (hClose, openTempFile)
import Test.Hspec

spec :: Spec
spec = describe "raw LocalTxSubmission" $ do
    it "negotiates an official node-to-client session and preserves payload bytes on AcceptTx" $ do
        captured <- newIORef []
        let payload = BS.pack [0x82, 0x01, 0x9f, 0x02, 0xff]
        withSubmissionServer 42 captured (TypedResponse SubmitSuccess) $ \socketPath -> do
            receipt <- submitRaw (matchingBinding socketPath 42) payload "conway" 3
            receiptOutcome receipt `shouldBe` Accepted
            receiptPayloadLength receipt `shouldBe` BS.length payload
            receiptTransactionEra receipt `shouldBe` "conway"
            receiptPayloadSha256 receipt `shouldBe`
                "735e86526f3c1b9938ca3420a2964b434c10eab7dd0b23e1b6e5508164c55ae5"
            receiptNegotiatedVersion receipt `shouldSatisfy` (/= Nothing)
            receiptRejectEvidence receipt `shouldBe` Nothing
        wire <- LBS.toStrict . LBS.concat <$> readIORef captured
        let expected = LBS.toStrict $ toLazyByteString $
                CBOR.encodeListLen 2 <> CBOR.encodeWord 0
                    <> CBOR.encodeListLen 2 <> CBOR.encodeWord 6
                    <> CBOR.encodeTag 24 <> CBOR.encodeBytes payload
        BS.unpack expected `isPrefixOf` BS.unpack wire `shouldBe` True

    it "reports only a typed RejectTx as node_rejected" $ do
        captured <- newIORef []
        withSubmissionServer 42 captured (TypedResponse (SubmitFail (ServerReject (TString "MissingRedeemers")))) $ \socketPath -> do
            receipt <- submitRaw (matchingBinding socketPath 42) (BS.pack [0x80]) "conway" 3
            receiptOutcome receipt `shouldBe` NodeRejected
            receiptRejection receipt `shouldSatisfy` maybe False ("MissingRedeemers" `isInfixOf`)

    it "retains exact official RejectTx response bytes and a typed opaque term tree" $ do
        requestBytes <- newIORef []
        responseBytes <- newIORef []
        let rejection = TList
                [ TString "MissingRedeemers"
                , TMap
                    [ (TString "script", TBytes (BS.pack [0x00, 0xff, 0x10]))
                    , (TInt 7, TTagged 24 (TList [TBool True, TNull]))
                    ]
                ]
        withSubmissionServerCapturingResponse
            42 requestBytes responseBytes
            (TypedResponse (SubmitFail (ServerReject rejection))) $ \socketPath -> do
                receipt <- submitRaw (matchingBinding socketPath 42) (BS.pack [0x80]) "conway" 3
                receiptOutcome receipt `shouldBe` NodeRejected
                evidence <- receiptRejectEvidence receipt `shouldReturnJust` "missing reject evidence"
                wire <- LBS.toStrict . LBS.concat <$> readIORef responseBytes
                rejectEvidenceStatus evidence `shouldBe` "complete"
                rejectWireScope evidence `shouldBe`
                    "localtxsubmission-mini-protocol-response-after-demux"
                rejectWireBytes evidence `shouldBe` Just wire
                rejectWireLength evidence `shouldBe` BS.length wire
                rejectWireSha256 evidence `shouldSatisfy`
                    maybe False ((== 64) . Text.length)
                rejectWireTruncated evidence `shouldBe` False
                rejectTermTree evidence `shouldBe` Just expectedNestedTree
                rejectTermNodeCount evidence `shouldBe` Just 10
                rejectTermMaxDepth evidence `shouldBe` Just 5
                let canonical = LBS.toStrict (toLazyByteString (encodeTerm rejection))
                rejectCanonicalCbor evidence `shouldBe` Just canonical
                rejectCanonicalCborLength evidence `shouldBe` Just (BS.length canonical)
                rejectCanonicalCborSha256 evidence `shouldSatisfy`
                    maybe False ((== 64) . Text.length)
                rejectCanonicalMatchesCapturedReject evidence `shouldBe` Nothing
                BSC.pack "\"value\":\"AP8Q\"" `BS.isInfixOf`
                    LBS.toStrict (encodeSubmissionReceipt receipt) `shouldBe` True

    it "hashes the full oversized RejectTx response without retaining its body" $ do
        requestBytes <- newIORef []
        responseBytes <- newIORef []
        let rejection = TBytes (BS.replicate 65537 0x5a)
        withSubmissionServerCapturingResponse
            42 requestBytes responseBytes
            (TypedResponse (SubmitFail (ServerReject rejection))) $ \socketPath -> do
                receipt <- submitRaw (matchingBinding socketPath 42) (BS.pack [0x80]) "conway" 3
                evidence <- receiptRejectEvidence receipt `shouldReturnJust` "missing reject evidence"
                wire <- LBS.toStrict . LBS.concat <$> readIORef responseBytes
                rejectEvidenceStatus evidence `shouldBe` "oversized"
                rejectWireBytes evidence `shouldBe` Nothing
                rejectWireLength evidence `shouldBe` BS.length wire
                rejectWireSha256 evidence `shouldBe` Just (wireHash wire)
                rejectWireTruncated evidence `shouldBe` True

    it "keeps typed rejection outcome while bounding wire and term evidence" $ do
        let deep = foldr (\_ acc -> TList [acc]) TNull [1 .. 65 :: Int]
            boundaryNodes = TList (replicate 4095 TNull)
            manyNodes = TList (replicate 4096 TNull)
            largeTree = TList (replicate 4095 (TBytes (BS.replicate 48 0x41)))
            deepEvidence = buildRejectEvidence
                (CaptureSnapshot 1 (Just (BS.singleton 0x80))) deep
            boundaryEvidence = buildRejectEvidence
                (CaptureSnapshot 1 (Just (BS.singleton 0x80))) boundaryNodes
            nodeEvidence = buildRejectEvidence
                (CaptureSnapshot 1 (Just (BS.singleton 0x80))) manyNodes
            outputEvidence = buildRejectEvidence
                (CaptureSnapshot 1 (Just (BS.singleton 0x80))) largeTree
        rejectEvidenceStatus deepEvidence `shouldBe` "term-unavailable"
        rejectFailureReason deepEvidence `shouldBe` Just "term-depth-exceeds-64"
        rejectTermNodeCount boundaryEvidence `shouldBe` Just 4096
        rejectEvidenceStatus nodeEvidence `shouldBe` "term-unavailable"
        rejectFailureReason nodeEvidence `shouldBe` Just "term-node-count-exceeds-4096"
        rejectEvidenceStatus outputEvidence `shouldBe` "term-unavailable"
        rejectFailureReason outputEvidence `shouldBe` Just "term-json-exceeds-262144-bytes"

    it "does not attach reject evidence to a malformed response transport failure" $ do
        captured <- newIORef []
        withSubmissionServer 42 captured (MalformedResponse (LBS.pack [0xff])) $ \socketPath -> do
            receipt <- submitRaw (matchingBinding socketPath 42) (BS.pack [0x80]) "conway" 3
            receiptOutcome receipt `shouldBe` TransportUnavailable
            receiptRejectEvidence receipt `shouldBe` Nothing

    it "keeps EOF without a protocol response transport-unavailable" $ do
        captured <- newIORef []
        withSubmissionServer 42 captured CloseWithoutResponse $ \socketPath -> do
            receipt <- submitRaw (matchingBinding socketPath 42) (BS.pack [0x80]) "conway" 3
            receiptOutcome receipt `shouldBe` TransportUnavailable
            receiptNegotiatedVersion receipt `shouldSatisfy` (/= Nothing)
            receiptRejection receipt `shouldBe` Nothing
            receiptDetail receipt `shouldBe` Just "connection-closed-after-negotiation"
            receiptRejectEvidence receipt `shouldBe` Nothing

    it "bounds a silent server as transport-unavailable" $ do
        captured <- newIORef []
        withSubmissionServer 42 captured (DelayResponse 2000000) $ \socketPath -> do
            receipt <- submitRaw (matchingBinding socketPath 42) (BS.pack [0x80]) "conway" 1
            receiptOutcome receipt `shouldBe` TransportUnavailable
            receiptDetail receipt `shouldBe` Just "timeout"
            receiptRejectEvidence receipt `shouldBe` Nothing

    it "rejects an ownership mismatch before opening the socket" $ do
        let binding = (matchingBinding "/definitely/not/a/socket" 42)
                { observedIdentity = (observedIdentity (matchingBinding "ignored" 42))
                    { exactInstallationId = "install-other" }
                }
        receipt <- submitRaw binding (BS.pack [0x80]) "conway" 1
        receiptOutcome receipt `shouldBe` IdentityRefused
        receiptNegotiatedVersion receipt `shouldBe` Nothing

    it "does not reinterpret a network-magic handshake refusal as node rejection" $ do
        captured <- newIORef []
        withSubmissionServer 42 captured (TypedResponse SubmitSuccess) $ \socketPath -> do
            receipt <- submitRaw (matchingBinding socketPath 43) (BS.pack [0x80]) "conway" 3
            receiptOutcome receipt `shouldBe` TransportUnavailable
            receiptRejection receipt `shouldBe` Nothing

data ServerBehavior
    = TypedResponse (SubmitResult ServerReject)
    | CloseWithoutResponse
    | DelayResponse Int
    | MalformedResponse LBS.ByteString

data ServerRawTx = ServerRawTx Word32 BS.ByteString
newtype ServerReject = ServerReject Term

instance ShowProxy ServerRawTx
instance ShowProxy ServerReject

withSubmissionServer
    :: Word32
    -> IORef [LBS.ByteString]
    -> ServerBehavior
    -> (FilePath -> IO a)
    -> IO a
withSubmissionServer magic captured behavior action =
    newIORef [] >>= \responses ->
        withSubmissionServerCapturingResponse magic captured responses behavior action

withSubmissionServerCapturingResponse
    :: Word32
    -> IORef [LBS.ByteString]
    -> IORef [LBS.ByteString]
    -> ServerBehavior
    -> (FilePath -> IO a)
    -> IO a
withSubmissionServerCapturingResponse magic captured responses behavior action =
    withTempSocketPath $ \socketPath ->
        withIOManager $ \ioManager -> do
            ready <- newEmptyMVar
            networkState <- newNetworkMutableState
            server <- async $
                withServerNode
                    (localSnocket ioManager)
                    makeLocalBearer
                    mempty
                    nullNetworkServerTracers
                    networkState
                    (AcceptedConnectionsLimit maxBound maxBound 0)
                    (localAddressFromPath socketPath)
                    nodeToClientHandshakeCodec
                    noTimeLimitsHandshake
                    (cborTermVersionDataCodec nodeToClientCodecCBORTerm)
                    (HandshakeCallbacks acceptableVersion queryVersion)
                    (serverVersions behavior captured responses (fromIntegral magic))
                    nullErrorPolicies
                    (\_ serverAsync -> putMVar ready () >> wait serverAsync)
            takeMVar ready
            action socketPath `finally` cancel server

serverVersions behavior captured responses magic =
    foldMapVersions oneVersion ([minBound .. maxBound] :: [NodeToClientVersion])
  where
    versionData = NodeToClientVersionData (NetworkMagic magic) False
    oneVersion version =
        simpleSingletonVersions version versionData $ \_ ->
            SomeResponderApplication (serverApplication behavior captured responses)

serverApplication behavior captured responses = OuroborosApplication
    [ MiniProtocol
        { miniProtocolNum = MiniProtocolNum 6
        , miniProtocolStart = StartOnDemand
        , miniProtocolLimits = MiniProtocolLimits 0xffffffff
        , miniProtocolRun = ResponderProtocolOnly $ MiniProtocolCb $ \context channel ->
            case behavior of
                CloseWithoutResponse -> do
                    _ <- recv channel
                    pure ((), Nothing)
                DelayResponse micros -> do
                    _ <- recv channel
                    threadDelay micros
                    pure ((), Nothing)
                MalformedResponse bytes -> do
                    _ <- recv channel
                    send channel bytes
                    pure ((), Nothing)
                TypedResponse response ->
                    runMiniProtocolCb
                        (typedResponder response)
                        context
                        (captureChannel captured responses channel)
        }
    ]
  where
    typedResponder response = mkMiniProtocolCbFromPeer $ \_ ->
        ( nullTracer
        , codecLocalTxSubmission encodeRawTx decodeRawTx encodeReject decodeReject
        , localTxSubmissionServerPeer $ pure $ server response
        )
    server response = LocalTxSubmissionServer
        { recvMsgSubmitTx = \_ -> pure (response, server response)
        , recvMsgDone = ()
        }

captureChannel received sent channel = Channel
    { send = \bytes -> do
        modifyIORef' sent (<> [bytes])
        send channel bytes
    , recv = do
        value <- recv channel
        maybe (pure ()) (\bytes -> modifyIORef' received (<> [bytes])) value
        pure value
    }

expectedNestedTree = RejectList
    [ RejectText "MissingRedeemers"
    , RejectMap
        [ (RejectText "script", RejectBytes (BS.pack [0x00, 0xff, 0x10]))
        , ( RejectInt 7
          , RejectTag 24 (RejectList [RejectBool True, RejectNull])
          )
        ]
    ]

shouldReturnJust :: Maybe a -> String -> IO a
shouldReturnJust (Just value) _ = pure value
shouldReturnJust Nothing message = expectationFailure message >> error message

wireHash :: BS.ByteString -> Text.Text
wireHash bytes = Text.pack (show (Crypto.hash bytes :: Crypto.Digest Crypto.SHA256))


encodeRawTx :: ServerRawTx -> CBOR.Encoding
encodeRawTx (ServerRawTx eraIndex payload) =
    CBOR.encodeListLen 2 <> CBOR.encodeWord32 eraIndex
        <> CBOR.encodeTag 24 <> CBOR.encodeBytes payload

decodeRawTx :: CBOR.Decoder s ServerRawTx
decodeRawTx = do
    size <- CBOR.decodeListLen
    if size /= 2 then fail "unexpected hard-fork transaction wrapper" else pure ()
    eraIndex <- CBOR.decodeWord32
    tag <- CBOR.decodeTag
    if tag /= 24 then fail "expected CBOR-in-CBOR tag 24" else pure ()
    payload <- CBOR.decodeBytes
    pure (ServerRawTx eraIndex payload)

encodeReject :: ServerReject -> CBOR.Encoding
encodeReject (ServerReject term) = encodeTerm term

decodeReject :: CBOR.Decoder s ServerReject
decodeReject = ServerReject <$> decodeTerm

matchingBinding :: FilePath -> Word32 -> IdentityBinding
matchingBinding socketPath magic = IdentityBinding exact exact
  where
    exact = ExactIdentity
        { exactInstallationId = "install-test"
        , exactComposeProject = "dwarf-substrate-test"
        , exactTopologyId = "dwarf-substrate-test"
        , exactNodeId = "node1"
        , exactContainerId = replicate 64 'a'
        , exactContainerImageId = "sha256:" <> replicate 64 'b'
        , exactSourceRevision = replicate 40 'c'
        , exactSocketPath = socketPath
        , exactNetworkMagic = fromIntegral magic
        }

withTempSocketPath :: (FilePath -> IO a) -> IO a
withTempSocketPath action = bracket acquire release action
  where
    acquire = do
        tmp <- getTemporaryDirectory
        (path, handle) <- openTempFile tmp "dwarf-localtx.sock"
        hClose handle
        removeFile path
        pure path
    release path = removeFile path `catchIO` (\_ -> pure ())

catchIO :: IO a -> (IOError -> IO a) -> IO a
catchIO = Exception.catch
