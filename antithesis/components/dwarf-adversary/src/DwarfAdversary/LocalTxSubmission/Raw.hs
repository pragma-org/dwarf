{-# LANGUAGE DataKinds #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE TypeApplications #-}

-- | One-shot, byte-preserving node-to-client LocalTxSubmission.
--
-- The transaction payload is emitted as opaque bytes inside the official
-- hard-fork era wrapper.  This module deliberately has no transaction decoder:
-- the only inbound value it decodes is the typed LocalTxSubmission rejection
-- term.
module DwarfAdversary.LocalTxSubmission.Raw
    ( CaptureSnapshot (..)
    , ExactIdentity (..)
    , IdentityBinding (..)
    , RejectEvidence (..)
    , RejectTermTree (..)
    , ReceiptOutcome (..)
    , RawSubmissionReceipt (..)
    , buildRejectEvidence
    , decodeSubmissionRequest
    , encodeSubmissionReceipt
    , submitRaw
    , validateIdentityBinding
    ) where

import Cardano.Crypto.Hash.Class qualified as Hash
import Cardano.Crypto.Hash.SHA256 (SHA256)
import Codec.CBOR.Decoding qualified as CBOR
import Codec.CBOR.Encoding qualified as CBOR
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Control.Exception (SomeException, try)
import Control.Tracer (nullTracer)
import Crypto.Hash qualified as Crypto
import Data.Bits ((.&.), (.|.), shiftR)
import Data.Aeson
    ( FromJSON (parseJSON)
    , ToJSON (toJSON)
    , Value
    , eitherDecodeStrict'
    , encode
    , object
    , withObject
    , (.:)
    , (.=)
    )
import Data.Aeson.Types qualified as Aeson
import Data.ByteString qualified as BS
import Data.ByteString.Base16 qualified as Base16
import Data.ByteString.Lazy qualified as LBS
import Data.Char (isHexDigit, toLower)
import Data.IORef (modifyIORef', newIORef, readIORef, writeIORef)
import Data.List (isInfixOf)
import Data.Text (Text)
import Data.Text qualified as Text
import Data.Text.Lazy qualified as LazyText
import Data.Text.Encoding qualified as Text
import Data.Word (Word32, Word64, Word8)
import Network.Mux.Channel (Channel (..))
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
    , connectTo
    , foldMapVersions
    , localSnocket
    , nullNetworkConnectTracers
    , simpleSingletonVersions
    , withIOManager
    )
import Ouroboros.Network.Protocol.LocalTxSubmission.Client
    ( LocalTxClientStIdle (..)
    , LocalTxSubmissionClient (..)
    , localTxSubmissionClientPeer
    )
import Ouroboros.Network.Protocol.LocalTxSubmission.Codec
    ( codecLocalTxSubmission
    )
import Ouroboros.Network.Protocol.LocalTxSubmission.Type
    ( SubmitResult (..)
    )
import Ouroboros.Network.Util.ShowProxy (ShowProxy)
import System.FilePath (isAbsolute)
import System.Timeout (timeout)

data ExactIdentity = ExactIdentity
    { exactInstallationId :: String
    , exactComposeProject :: String
    , exactTopologyId :: String
    , exactNodeId :: String
    , exactContainerId :: String
    , exactContainerImageId :: String
    , exactSourceRevision :: String
    , exactSocketPath :: FilePath
    , exactNetworkMagic :: Word32
    }
    deriving (Eq, Show)

data IdentityBinding = IdentityBinding
    { expectedIdentity :: ExactIdentity
    , observedIdentity :: ExactIdentity
    }
    deriving (Eq, Show)

data ReceiptOutcome
    = Accepted
    | NodeRejected
    | IdentityRefused
    | DecodeClose
    | TransportUnavailable
    | Unknown
    deriving (Eq, Show)

data RawSubmissionReceipt = RawSubmissionReceipt
    { receiptOutcome :: ReceiptOutcome
    , receiptPayloadSha256 :: Text
    , receiptPayloadLength :: Int
    , receiptNetworkMagic :: Word32
    , receiptTransactionEra :: Text
    , receiptNegotiatedVersion :: Maybe Text
    , receiptRejection :: Maybe String
    , receiptDetail :: Maybe Text
    , receiptRejectEvidence :: Maybe RejectEvidence
    }
    deriving (Eq, Show)

data CaptureSnapshot = CaptureSnapshot Int (Maybe BS.ByteString)
    deriving (Eq, Show)

data RejectTermTree
    = RejectInt Int
    | RejectInteger Integer
    | RejectBytes BS.ByteString
    | RejectBytesIndefinite LBS.ByteString
    | RejectText Text
    | RejectTextIndefinite LazyText.Text
    | RejectList [RejectTermTree]
    | RejectListIndefinite [RejectTermTree]
    | RejectMap [(RejectTermTree, RejectTermTree)]
    | RejectMapIndefinite [(RejectTermTree, RejectTermTree)]
    | RejectTag Word64 RejectTermTree
    | RejectBool Bool
    | RejectNull
    | RejectSimple Word8
    | RejectHalf Text
    | RejectFloat Text
    | RejectDouble Text
    deriving (Eq, Show)

data RejectEvidence = RejectEvidence
    { rejectEvidenceStatus :: Text
    , rejectWireScope :: Text
    , rejectWireBytes :: Maybe BS.ByteString
    , rejectWireLength :: Int
    , rejectWireSha256 :: Maybe Text
    , rejectWireTruncated :: Bool
    , rejectTermTree :: Maybe RejectTermTree
    , rejectTermNodeCount :: Maybe Int
    , rejectTermMaxDepth :: Maybe Int
    , rejectCanonicalCbor :: Maybe BS.ByteString
    , rejectCanonicalCborLength :: Maybe Int
    , rejectCanonicalCborSha256 :: Maybe Text
    , rejectCanonicalMatchesCapturedReject :: Maybe Bool
    , rejectFailureReason :: Maybe Text
    }
    deriving (Eq, Show)

data WireRequest = WireRequest
    { wireSchemaVersion :: Int
    , wireBinding :: IdentityBinding
    , wirePayload :: BS.ByteString
    , wireTransactionEra :: Text
    , wireTimeoutSeconds :: Int
    }

newtype RawTx = RawTx BS.ByteString
newtype RawReject = RawReject Term

instance ShowProxy RawTx
instance ShowProxy RawReject

data ProtocolResult = ProtocolResult
    { protocolOutcome :: ReceiptOutcome
    , protocolVersion :: Text
    , protocolMagic :: Word32
    , protocolRejection :: Maybe String
    , protocolRejectTerm :: Maybe Term
    }

instance FromJSON ExactIdentity where
    parseJSON = withObject "ExactIdentity" $ \body ->
        ExactIdentity
            <$> body .: "installation_id"
            <*> body .: "compose_project"
            <*> body .: "topology_id"
            <*> body .: "node_id"
            <*> body .: "container_id"
            <*> body .: "container_image_id"
            <*> body .: "source_revision"
            <*> body .: "socket_path"
            <*> body .: "network_magic"

instance FromJSON IdentityBinding where
    parseJSON = withObject "IdentityBinding" $ \body ->
        IdentityBinding
            <$> body .: "expected_identity"
            <*> body .: "observed_identity"

instance ToJSON ReceiptOutcome where
    toJSON = toJSON . outcomeText

instance ToJSON RawSubmissionReceipt where
    toJSON receipt = object $
        [ "schema_version" .= (1 :: Int)
        , "outcome" .= receiptOutcome receipt
        , "payload_sha256" .= receiptPayloadSha256 receipt
        , "payload_length" .= receiptPayloadLength receipt
        , "network_magic" .= receiptNetworkMagic receipt
        , "transaction_era" .= receiptTransactionEra receipt
        , "negotiated_version" .= receiptNegotiatedVersion receipt
        , "rejection" .= receiptRejection receipt
        , "detail" .= receiptDetail receipt
        ] <> maybe [] (pure . ("reject_evidence" .=)) (receiptRejectEvidence receipt)

instance ToJSON RejectEvidence where
    toJSON evidence = object
        [ "schema_version" .= (1 :: Int)
        , "status" .= rejectEvidenceStatus evidence
        , "wire_scope" .= rejectWireScope evidence
        , "wire_encoding" .= ("base64" :: Text)
        , "wire_bytes_base64" .= (base64Text <$> rejectWireBytes evidence)
        , "wire_length" .= rejectWireLength evidence
        , "wire_sha256" .= rejectWireSha256 evidence
        , "wire_capture_limit" .= maxWireCaptureBytes
        , "wire_truncated" .= rejectWireTruncated evidence
        , "term_tree_schema_version" .= (1 :: Int)
        , "term_tree" .= rejectTermTree evidence
        , "term_node_count" .= rejectTermNodeCount evidence
        , "term_max_depth" .= rejectTermMaxDepth evidence
        , "term_depth_limit" .= maxTermDepth
        , "term_node_limit" .= maxTermNodes
        , "term_json_size_limit" .= maxTermJsonBytes
        , "canonical_cbor_scope" .= ("decoded-reject-term-canonical-cbor" :: Text)
        , "canonical_cbor_encoding" .= ("base64" :: Text)
        , "canonical_cbor_base64" .= (base64Text <$> rejectCanonicalCbor evidence)
        , "canonical_cbor_length" .= rejectCanonicalCborLength evidence
        , "canonical_cbor_sha256" .= rejectCanonicalCborSha256 evidence
        , "canonical_matches_captured_reject" .= rejectCanonicalMatchesCapturedReject evidence
        , "failure_reason" .= rejectFailureReason evidence
        ]

instance ToJSON RejectTermTree where
    toJSON = \case
        RejectInt value -> scalar "int" value
        RejectInteger value -> scalar "integer" value
        RejectBytes value -> bytes "bytes" value
        RejectBytesIndefinite value -> object
            [ "type" .= ("bytes_indefinite" :: Text)
            , "encoding" .= ("base64" :: Text)
            , "value" .= base64Text (LBS.toStrict value)
            ]
        RejectText value -> scalar "text" value
        RejectTextIndefinite value -> object
            [ "type" .= ("text_indefinite" :: Text)
            , "value" .= LazyText.toStrict value
            ]
        RejectList values -> object
            [ "type" .= ("list" :: Text), "items" .= values ]
        RejectListIndefinite values -> object
            [ "type" .= ("list_indefinite" :: Text), "items" .= values ]
        RejectMap values -> object
            [ "type" .= ("map" :: Text), "entries" .= fmap entry values ]
        RejectMapIndefinite values -> object
            [ "type" .= ("map_indefinite" :: Text), "entries" .= fmap entry values ]
        RejectTag tag value -> object
            [ "type" .= ("tag" :: Text), "tag" .= tag, "value" .= value ]
        RejectBool value -> scalar "bool" value
        RejectNull -> object ["type" .= ("null" :: Text)]
        RejectSimple value -> scalar "simple" value
        RejectHalf value -> scalar "half" value
        RejectFloat value -> scalar "float" value
        RejectDouble value -> scalar "double" value
      where
        scalar kind value = object ["type" .= (kind :: Text), "value" .= value]
        bytes kind value = object
            [ "type" .= (kind :: Text)
            , "encoding" .= ("base64" :: Text)
            , "value" .= base64Text value
            ]
        entry (key, value) = object ["key" .= key, "value" .= value]

decodeSubmissionRequest :: BS.ByteString -> Either String (IdentityBinding, BS.ByteString, Text, Int)
decodeSubmissionRequest raw = do
    body <- eitherDecodeStrict' raw :: Either String Value
    request <- case body of
        value -> parseEitherWire value
    if wireSchemaVersion request /= 1
        then Left "unsupported request schema"
        else Right
            ( wireBinding request
            , wirePayload request
            , wireTransactionEra request
            , wireTimeoutSeconds request
            )
  where
    parseEitherWire value =
        case Aeson.parseEither parseWireRequest value of
            Left message -> Left message
            Right request -> Right request

parseWireRequest value = withObject "RawSubmissionRequest" (\body -> do
    schemaVersion <- body .: "schema_version"
    expected <- body .: "expected_identity"
    observed <- body .: "observed_identity"
    payloadHex <- body .: "payload_hex"
    transactionEra <- body .: "transaction_era"
    timeoutSeconds <- body .: "timeout_seconds"
    payload <- either fail pure (Base16.decode (Text.encodeUtf8 payloadHex))
    if BS.length payload > 65536
        then fail "payload exceeds 65536 bytes"
        else if transactionEra /= ("conway" :: Text)
            then fail "unsupported transaction era"
        else pure WireRequest
            { wireSchemaVersion = schemaVersion
            , wireBinding = IdentityBinding expected observed
            , wirePayload = payload
            , wireTransactionEra = transactionEra
            , wireTimeoutSeconds = timeoutSeconds
            }) value

encodeSubmissionReceipt :: RawSubmissionReceipt -> LBS.ByteString
encodeSubmissionReceipt = encode

validateIdentityBinding :: IdentityBinding -> Either Text ExactIdentity
validateIdentityBinding IdentityBinding{expectedIdentity, observedIdentity}
    | expectedIdentity /= observedIdentity = Left "expected and observed identities differ"
    | null (exactInstallationId expectedIdentity) = Left "installation identity is missing"
    | null (exactComposeProject expectedIdentity) = Left "compose project is missing"
    | null (exactTopologyId expectedIdentity) = Left "topology identity is missing"
    | null (exactNodeId expectedIdentity) = Left "node identity is missing"
    | not (hexOfLength 64 (exactContainerId expectedIdentity)) = Left "container identity is invalid"
    | not (imageDigest (exactContainerImageId expectedIdentity)) = Left "container image identity is invalid"
    | not (hexOfLength 40 (exactSourceRevision expectedIdentity)) = Left "source revision is invalid"
    | not (isAbsolute (exactSocketPath expectedIdentity)) = Left "socket path is not absolute"
    | otherwise = Right expectedIdentity
  where
    hexOfLength count value = length value == count && all isHexDigit value
    imageDigest value = case Text.stripPrefix "sha256:" (Text.pack value) of
        Just digest -> Text.length digest == 64 && Text.all isHexDigit digest
        Nothing -> False

submitRaw :: IdentityBinding -> BS.ByteString -> Text -> Int -> IO RawSubmissionReceipt
submitRaw binding payload transactionEra timeoutSeconds =
    case validateIdentityBinding binding of
        Left reason -> pure (baseReceipt IdentityRefused Nothing Nothing (Just reason) Nothing)
        Right identity
            | BS.length payload > 65536 ->
                pure (baseReceipt IdentityRefused Nothing Nothing (Just "payload exceeds 65536 bytes") Nothing)
            | transactionEra /= "conway" ->
                pure (baseReceipt IdentityRefused Nothing Nothing (Just "unsupported transaction era") Nothing)
            | timeoutSeconds < 1 || timeoutSeconds > 120 ->
                pure (baseReceipt IdentityRefused Nothing Nothing (Just "timeout is outside 1..120 seconds") Nothing)
            | otherwise -> do
                negotiatedVersion <- newIORef Nothing
                responseCapture <- newIORef (CaptureSnapshot 0 (Just BS.empty))
                responseHash <- newIORef (Crypto.hashInit :: Crypto.Context Crypto.SHA256)
                result <- timeout (timeoutSeconds * 1000000) $
                    try $ withIOManager $ \ioManager ->
                        connectTo
                            (localSnocket ioManager)
                            nullNetworkConnectTracers
                            (clientVersions identity payload negotiatedVersion responseCapture responseHash)
                            (exactSocketPath identity)
                observedVersion <- readIORef negotiatedVersion
                capturedResponse <- readIORef responseCapture
                capturedResponseHash <- captureHashText <$> readIORef responseHash
                pure $ case result of
                    Nothing -> baseReceipt TransportUnavailable observedVersion Nothing (Just "timeout") Nothing
                    Just (Left (exception :: SomeException)) ->
                        baseReceipt TransportUnavailable observedVersion Nothing
                            (Just (transportDetail observedVersion (show exception))) Nothing
                    Just (Right (Left exception)) ->
                        let message = show exception
                            outcome = if decoderFailure message then DecodeClose else TransportUnavailable
                            detail = if outcome == DecodeClose
                                then detailFor outcome
                                else transportDetail observedVersion message
                        in baseReceipt outcome observedVersion Nothing (Just detail) Nothing
                    Just (Right (Right protocol)) ->
                        baseReceipt
                            (protocolOutcome protocol)
                            (Just (protocolVersion protocol))
                            (protocolRejection protocol)
                            Nothing
                            ( buildRejectEvidenceWithWireHash
                                capturedResponse
                                (Just capturedResponseHash)
                                <$> protocolRejectTerm protocol
                            )
  where
    identity = expectedIdentity binding
    baseReceipt outcome version rejection detail evidence = RawSubmissionReceipt
        { receiptOutcome = outcome
        , receiptPayloadSha256 = payloadHash payload
        , receiptPayloadLength = BS.length payload
        , receiptNetworkMagic = exactNetworkMagic identity
        , receiptTransactionEra = transactionEra
        , receiptNegotiatedVersion = version
        , receiptRejection = rejection
        , receiptDetail = detail
        , receiptRejectEvidence = evidence
        }
    decoderFailure message = any (`isInfixOf` message)
        ["DeserialiseFailure", "CodecFailure", "decoder"]
    transportDetail observedVersion message
        | "timeout" `isInfixOf` map toLower message = "timeout"
        | otherwise = case observedVersion of
            Just _ -> "connection-closed-after-negotiation"
            Nothing -> "connection-error"
    detailFor DecodeClose = "response-decode-failed"
    detailFor _ = "connection-closed-without-protocol-response"

clientVersions identity payload negotiatedVersion responseCapture responseHash =
    foldMapVersions oneVersion ([minBound .. maxBound] :: [NodeToClientVersion])
  where
    versionData = NodeToClientVersionData (NetworkMagic (exactNetworkMagic identity)) False
    oneVersion version =
        simpleSingletonVersions version versionData $ \negotiated ->
            clientApplication
                version negotiated payload negotiatedVersion responseCapture responseHash

clientApplication version negotiated payload negotiatedVersion responseCapture responseHash = OuroborosApplication
    [ MiniProtocol
        { miniProtocolNum = MiniProtocolNum 6
        , miniProtocolStart = StartOnDemand
        , miniProtocolLimits = MiniProtocolLimits 0xffffffff
        , miniProtocolRun = InitiatorProtocolOnly $ MiniProtocolCb $ \context channel ->
            runMiniProtocolCb
                (mkMiniProtocolCbFromPeer $ \_ ->
                    ( nullTracer
                    , rawClientCodec
                    , localTxSubmissionClientPeer
                        (rawClient version negotiated payload negotiatedVersion)
                    ))
                context
                (captureInboundChannel responseCapture responseHash channel)
        }
    ]

rawClient version negotiated payload negotiatedVersion = LocalTxSubmissionClient $ do
    writeIORef negotiatedVersion (Just (Text.pack (show version)))
    pure $ SendMsgSubmitTx (RawTx payload) $ \case
        SubmitSuccess -> pure $ SendMsgDone ProtocolResult
            { protocolOutcome = Accepted
            , protocolVersion = Text.pack (show version)
            , protocolMagic = magic negotiated
            , protocolRejection = Nothing
            , protocolRejectTerm = Nothing
            }
        SubmitFail (RawReject reason) -> pure $ SendMsgDone ProtocolResult
            { protocolOutcome = NodeRejected
            , protocolVersion = Text.pack (show version)
            , protocolMagic = magic negotiated
            , protocolRejection = Just (take 4096 (show reason))
            , protocolRejectTerm = Just reason
            }
  where
    magic NodeToClientVersionData{networkMagic = NetworkMagic value} = value

rawClientCodec = codecLocalTxSubmission
    (\(RawTx bytes) ->
        CBOR.encodeListLen 2
            <> CBOR.encodeWord 6
            <> CBOR.encodeTag 24
            <> CBOR.encodeBytes bytes)
    (fail "raw LocalTxSubmission client never decodes a transaction")
    (\(RawReject term) -> encodeTerm term)
    (RawReject <$> decodeTerm)

captureInboundChannel responseCapture responseHash channel = Channel
    { send = send channel
    , recv = do
        value <- recv channel
        maybe (pure ()) (captureChunk responseCapture responseHash) value
        pure value
    }

captureChunk responseCapture responseHash chunk =
    let strict = LBS.toStrict chunk
    in do
        modifyIORef' responseHash (`Crypto.hashUpdate` strict)
        modifyIORef' responseCapture $ \(CaptureSnapshot total retained) ->
            let total' = total + BS.length strict
                retained' = case retained of
                    Just bytes | total' <= maxWireCaptureBytes -> Just (bytes <> strict)
                    _ -> Nothing
            in CaptureSnapshot total' retained'

buildRejectEvidence :: CaptureSnapshot -> Term -> RejectEvidence
buildRejectEvidence snapshot@(CaptureSnapshot _ wireBytes) =
    buildRejectEvidenceWithWireHash snapshot (payloadHash <$> wireBytes)

buildRejectEvidenceWithWireHash
    :: CaptureSnapshot -> Maybe Text -> Term -> RejectEvidence
buildRejectEvidenceWithWireHash (CaptureSnapshot wireLength wireBytes) wireHash term = RejectEvidence
    { rejectEvidenceStatus = status
    , rejectWireScope = "localtxsubmission-mini-protocol-response-after-demux"
    , rejectWireBytes = exactWire
    , rejectWireLength = wireLength
    , rejectWireSha256 = wireHash
    , rejectWireTruncated = wireLength > maxWireCaptureBytes
    , rejectTermTree = tree
    , rejectTermNodeCount = nodeCount
    , rejectTermMaxDepth = termDepth
    , rejectCanonicalCbor = canonical
    , rejectCanonicalCborLength = BS.length <$> canonical
    , rejectCanonicalCborSha256 = payloadHash <$> canonical
    , rejectCanonicalMatchesCapturedReject = Nothing
    , rejectFailureReason = failureReason
    }
  where
    exactWire
        | wireLength > maxWireCaptureBytes = Nothing
        | otherwise = wireBytes
    canonicalPrefix = LBS.toStrict . LBS.take (fromIntegral maxTermJsonBytes + 1) $
        toLazyByteString (encodeTerm term)
    canonical
        | BS.length canonicalPrefix > maxTermJsonBytes = Nothing
        | otherwise = Just canonicalPrefix
    treeResult = do
        (candidate, nodes, depth) <- boundedTermTree term
        if LBS.length (encode candidate) > fromIntegral maxTermJsonBytes
            then Left "term-json-exceeds-262144-bytes"
            else Right (candidate, nodes, depth)
    tree = either (const Nothing) (Just . firstOfThree) treeResult
    nodeCount = either (const Nothing) (Just . secondOfThree) treeResult
    termDepth = either (const Nothing) (Just . thirdOfThree) treeResult
    treeFailure = either Just (const Nothing) treeResult
    canonicalFailure
        | canonical == Nothing = Just "canonical-cbor-exceeds-262144-bytes"
        | otherwise = Nothing
    wireFailure
        | wireLength > maxWireCaptureBytes = Just "wire-response-exceeds-65536-bytes"
        | wireBytes == Nothing = Just "wire-response-unavailable"
        | otherwise = Nothing
    failureReason = treeFailure <> canonicalFailure <> wireFailure
    status
        | treeFailure /= Nothing || canonicalFailure /= Nothing = "term-unavailable"
        | wireLength > maxWireCaptureBytes = "oversized"
        | wireBytes == Nothing = "wire-unavailable"
        | otherwise = "complete"

boundedTermTree :: Term -> Either Text (RejectTermTree, Int, Int)
boundedTermTree term = do
    (tree, nodes, depth, _) <- go 1 maxTermNodes term
    pure (tree, nodes, depth)
  where
    go depth remaining term
        | depth > maxTermDepth = Left "term-depth-exceeds-64"
        | remaining < 1 = Left "term-node-count-exceeds-4096"
        | otherwise = case term of
            TInt value -> leaf depth (RejectInt value)
            TInteger value -> leaf depth (RejectInteger value)
            TBytes value -> leaf depth (RejectBytes value)
            TBytesI values -> leaf depth (RejectBytesIndefinite values)
            TString value -> leaf depth (RejectText value)
            TStringI values -> leaf depth (RejectTextIndefinite values)
            TList values -> branch depth RejectList values
            TListI values -> branch depth RejectListIndefinite values
            TMap values -> mapBranch depth RejectMap values
            TMapI values -> mapBranch depth RejectMapIndefinite values
            TTagged tag value -> do
                (child, nodes, childDepth, childRemaining) <-
                    go (depth + 1) (remaining - 1) value
                pure
                    ( RejectTag tag child
                    , 1 + nodes
                    , max depth childDepth
                    , childRemaining
                    )
            TBool value -> leaf depth (RejectBool value)
            TNull -> leaf depth RejectNull
            TSimple value -> leaf depth (RejectSimple value)
            THalf value -> leaf depth (RejectHalf (Text.pack (show value)))
            TFloat value -> leaf depth (RejectFloat (Text.pack (show value)))
            TDouble value -> leaf depth (RejectDouble (Text.pack (show value)))
      where
        leaf leafDepth value = Right (value, 1, leafDepth, remaining - 1)
        branch branchDepth constructor values = do
            (children, childNodes, childDepth, finalRemaining) <-
                traverseChildren (branchDepth + 1) (remaining - 1) values
            pure
                ( constructor children
                , 1 + childNodes
                , max branchDepth childDepth
                , finalRemaining
                )
        mapBranch branchDepth constructor values = do
            (entries, childNodes, childDepth, finalRemaining) <-
                traverseEntries (branchDepth + 1) (remaining - 1) values
            pure
                ( constructor entries
                , 1 + childNodes
                , max branchDepth childDepth
                , finalRemaining
                )
    traverseChildren _ remaining [] = Right ([], 0, 0, remaining)
    traverseChildren depth remaining (value : values) = do
        (tree, nodes, treeDepth, nextRemaining) <- go depth remaining value
        (trees, laterNodes, laterDepth, finalRemaining) <-
            traverseChildren depth nextRemaining values
        pure
            ( tree : trees
            , nodes + laterNodes
            , max treeDepth laterDepth
            , finalRemaining
            )
    traverseEntries _ remaining [] = Right ([], 0, 0, remaining)
    traverseEntries depth remaining ((key, value) : entries) = do
        (keyTree, keyNodes, keyDepth, afterKey) <- go depth remaining key
        (valueTree, valueNodes, valueDepth, afterValue) <- go depth afterKey value
        (laterEntries, laterNodes, laterDepth, finalRemaining) <-
            traverseEntries depth afterValue entries
        pure
            ( (keyTree, valueTree) : laterEntries
            , keyNodes + valueNodes + laterNodes
            , maximum [keyDepth, valueDepth, laterDepth]
            , finalRemaining
            )

captureHashText :: Crypto.Context Crypto.SHA256 -> Text
captureHashText context =
    Text.pack (show (Crypto.hashFinalize context :: Crypto.Digest Crypto.SHA256))

firstOfThree (value, _, _) = value
secondOfThree (_, value, _) = value
thirdOfThree (_, _, value) = value

maxWireCaptureBytes, maxTermDepth, maxTermNodes, maxTermJsonBytes :: Int
maxWireCaptureBytes = 65536
maxTermDepth = 64
maxTermNodes = 4096
maxTermJsonBytes = 262144

base64Text :: BS.ByteString -> Text
base64Text = Text.pack . encodeBytes . BS.unpack
  where
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    charAt value = alphabet !! value
    encodeBytes (a : b : c : rest) =
        charAt (fromIntegral (a `shiftR` 2))
            : charAt (fromIntegral (((a .&. 0x03) `shiftLWord8` 4) .|. (b `shiftR` 4)))
            : charAt (fromIntegral (((b .&. 0x0f) `shiftLWord8` 2) .|. (c `shiftR` 6)))
            : charAt (fromIntegral (c .&. 0x3f))
            : encodeBytes rest
    encodeBytes [a, b] =
        [ charAt (fromIntegral (a `shiftR` 2))
        , charAt (fromIntegral (((a .&. 0x03) `shiftLWord8` 4) .|. (b `shiftR` 4)))
        , charAt (fromIntegral ((b .&. 0x0f) `shiftLWord8` 2))
        , '='
        ]
    encodeBytes [a] =
        [ charAt (fromIntegral (a `shiftR` 2))
        , charAt (fromIntegral ((a .&. 0x03) `shiftLWord8` 4))
        , '='
        , '='
        ]
    encodeBytes [] = []
    shiftLWord8 value amount = fromIntegral ((fromIntegral value :: Int) * (2 ^ amount))

payloadHash :: BS.ByteString -> Text
payloadHash bytes = Hash.hashToTextAsHex (Hash.hashWith @SHA256 id bytes)

outcomeText :: ReceiptOutcome -> Text
outcomeText = \case
    Accepted -> "accepted"
    NodeRejected -> "node_rejected"
    IdentityRefused -> "identity_refused"
    DecodeClose -> "decode_close"
    TransportUnavailable -> "transport_unavailable"
    Unknown -> "unknown"
