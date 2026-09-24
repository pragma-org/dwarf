{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}

module Main (main) where

import Codec.CBOR.Write (toLazyByteString)
import Control.Concurrent (forkIO, threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (newTVarIO)
import Control.Exception (SomeException, catch)
import Control.Monad (forM_, forever)
import Data.Aeson (Value, eitherDecode, encode, object, (.=))
import Data.ByteString.Lazy qualified as LBS
import Data.ByteString.Lazy.Char8 qualified as LBS8
import Data.IORef (IORef, atomicModifyIORef', newIORef, readIORef, writeIORef)
import Data.Set qualified as Set
import DwarfAdversary (originPoint)
import DwarfAdversary.Application (Limit (..), runChainProducerInto, syncHeaders)
import DwarfAdversary.ChainSync.Codec
    ( Header
    , Point
    , Tip
    , decHeader
    , decPoint
    , decTip
    , encHeader
    , encPoint
    , encTip
    )
import DwarfAdversary.ChainSync.Connection
    ( onDemandBlockFetchResponder
    , plainBlockFetchCodec
    , runChainSyncServer
    )
import DwarfAdversary.ChainSync.Server (advancingChainSyncServer, caseInjectingChainSyncServer)
import DwarfAdversary.SDK qualified as SDK
import DwarfOpcertAdversary
    ( CaseResult (..)
    , KeySet (..)
    , ReSignOutcome (..)
    , ReSignParams (..)
    , CaseSpec (..)
    , applyCase
    , applyCaseSpec
    , parseCaseSpec
    , reEncodeOpcert
    , caseEligible
    , caseMutationName
    , loadColdSignKey
    , loadKesSignKey
    , plainCodec
    , reSignHeader
    , resignCodec
    )
import Cardano.Slotting.Slot (SlotNo (..))
import Codec.CBOR.Encoding (encodePreEncoded)
import Codec.Serialise (DeserialiseFailure)
import Data.IORef (IORef)
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Network.Protocol.ChainSync.Codec qualified as ChainSyncCodec
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)
import System.IO.Unsafe (unsafePerformIO)
import Ouroboros.Network.Block (HeaderFields (..), getHeaderFields)
import Ouroboros.Network.Magic (NetworkMagic (..))
import Ouroboros.Network.Mock.Chain qualified as Chain
import System.Directory (createDirectoryIfMissing, doesFileExist, getFileSize)
import System.Environment (getArgs)
import System.FilePath (takeDirectory)
import System.IO (BufferMode (LineBuffering), IOMode (AppendMode), hSetBuffering, stdout, withBinaryFile)
import Text.Read (readMaybe)


main :: IO ()
main = do
    hSetBuffering stdout LineBuffering
    args <- getArgs
    case args of
        ["verify-resign", host, portText, countText, kesSkey, slotsText] ->
            case (readMaybe portText, readMaybe countText, readMaybe slotsText) of
                (Just port, Just count, Just slots) | count >= 2 ->
                    runVerify host port count kesSkey slots
                _ -> usage
        [ "live-proxy"
            , "--upstream", upstream
            , "--listen-port", listenText
            , "--kes-skey", kesSkey
            , "--slots-per-kes", slotsText
            , "--evidence", evidence
            ] ->
                case (parseHostPort upstream, readMaybe listenText, readMaybe slotsText) of
                    (Just (host, upstreamPort), Just listenPort, Just slots) ->
                        runLive host upstreamPort listenPort kesSkey slots evidence
                    _ -> usage
        ("serve-case" : rest) ->
            let flags = parseFlags rest
             in case ( lookupFlag "--case" flags
                     , lookupFlag "--upstream" flags >>= parseHostPort
                     , lookupFlag "--listen-port" flags >>= readMaybe
                     , lookupFlag "--kes-skey" flags
                     , lookupFlag "--cold-skey" flags
                     , lookupFlag "--slots-per-kes" flags >>= readMaybe
                     , lookupFlag "--evidence" flags
                     ) of
                    (Just caseId, Just (host, upstreamPort), Just listenPort, Just kesSkey, Just coldSkey, Just slots, Just evidence) ->
                        let maxEvo = maybe 60 id (lookupFlag "--max-kes-evo" flags >>= readMaybe)
                            caseSpecPath = lookupFlag "--case-spec" flags
                         in runCase caseId host upstreamPort listenPort kesSkey coldSkey slots maxEvo evidence caseSpecPath
                    _ -> usage
        _ -> usage


usage :: IO a
usage =
    error
        "usage:\n\
        \  dwarf-opcert-adversary verify-resign HOST PORT COUNT KES_SKEY SLOTS_PER_KES\n\
        \  dwarf-opcert-adversary live-proxy --upstream HOST:PORT --listen-port PORT --kes-skey FILE --slots-per-kes N --evidence FILE\n\
        \  dwarf-opcert-adversary serve-case --case CASE_ID --upstream HOST:PORT --listen-port PORT --kes-skey FILE --cold-skey FILE --slots-per-kes N --max-kes-evo N --evidence FILE [--case-spec FILE]"


parseHostPort :: String -> Maybe (String, Int)
parseHostPort value =
    case break (== ':') value of
        (host, ':' : portText) | not (null host) -> do
            port <- readMaybe portText
            pure (host, port)
        _ -> Nothing


-- | Offline proof of the re-sign pipeline: capture live headers, re-sign the
-- first one that belongs to our pool, and confirm the re-signed bytes are a
-- byte-identical, valid header (deterministic KES => same signature => same
-- header hash). No node required.
runVerify :: String -> Int -> Int -> FilePath -> Word -> IO ()
runVerify host port count kesSkey slots = do
    kesKey <- loadKesSignKey kesSkey
    let params = ReSignParams kesKey slots
    captured <- syncHeaders (NetworkMagic 42) host (fromIntegral port) originPoint (Limit (fromIntegral count))
    headers <- either (error . show) pure captured
    putStrLn $ "verify-resign: captured " <> show (length headers) <> " headers"
    let results = [(h, reSignHeader params h) | h <- headers]
        mine = [(h, r, o) | (h, (r, o)) <- results, isResigned o]
    case mine of
        [] -> do
            forM_ results $ \(h, (_, o)) ->
                putStrLn $ "  header " <> show (getHeaderFields h) <> " => " <> show o
            error "verify-resign: no captured header belongs to our pool (hot-key mismatch on all)"
        ((orig, resigned, outcome) : _) -> do
            let origBytes = LBS.toStrict (toLazyByteString (encHeader orig))
                newBytes = LBS.toStrict (toLazyByteString (encHeader resigned))
                HeaderFields origSlot origBlk origHash = getHeaderFields orig
                HeaderFields _ _ newHash = getHeaderFields resigned
            putStrLn $ "verify-resign: outcome=" <> show outcome
            putStrLn $ "  slot=" <> show origSlot <> " block=" <> show origBlk
            putStrLn $ "  original hash = " <> show origHash
            putStrLn $ "  resigned hash = " <> show newHash
            if origBytes == newBytes
                then putStrLn "verify-resign: RESIGNED BYTES IDENTICAL TO LIVE HEADER (valid-control OK)"
                else do
                    putStrLn $ "verify-resign: bytes differ (orig " <> show (length' origBytes)
                        <> " vs resigned " <> show (length' newBytes) <> ")"
                    if origHash == newHash
                        then putStrLn "verify-resign: hashes equal though byte framing differs (still valid-control)"
                        else error "verify-resign: hash CHANGED - re-sign did not reproduce a valid header"
  where
    isResigned = \case ReSignedConway _ -> True; _ -> False
    length' = LBS.length . LBS.fromStrict


runLive :: String -> Int -> Int -> FilePath -> Word -> FilePath -> IO ()
runLive host upstreamPort listenPort kesSkey slots evidence = do
    kesKey <- loadKesSignKey kesSkey
    let params = ReSignParams kesKey slots
        magic = NetworkMagic 42
    chainVar <- newTVarIO Chain.Genesis
    seenHeaders <- newIORef Set.empty
    acceptedOnce <- newIORef False
    SDK.reachable
        "opcert_valid_control_proxy_started"
        (object ["upstream" .= (host <> ":" <> show upstreamPort), "slots_per_kes" .= slots])
    appendEvidence evidence $
        object ["kind" .= ("proxy_started" :: String), "slots_per_kes" .= slots]
    _ <- forkIO $ forever $ do
        result <- runChainProducerInto chainVar magic host (fromIntegral upstreamPort)
        case result of
            Left exception -> putStrLn ("opcert-live: upstream ended: " <> show exception)
            Right () -> putStrLn "opcert-live: upstream ended cleanly"
        threadDelay 1_000_000
    let onAccept peer = do
            first <- atomicModifyIORef' acceptedOnce (\seen -> (True, not seen))
            if first
                then do
                    putStrLn ("opcert-live: accepted " <> peer)
                    SDK.reachable "opcert_victim_connected" (object ["peer" .= peer])
                else pure ()
        onServe = emitHeaderEvidence params seenHeaders evidence
        server = advancingChainSyncServer (const (pure ())) onServe chainVar
    forever $
        ( runChainSyncServer
            magic
            (fromIntegral listenPort)
            onAccept
            (resignCodec params)
            server
            plainBlockFetchCodec
            (onDemandBlockFetchResponder putStrLn (const (pure ())) magic (host, upstreamPort) chainVar)
            >> pure ()
        ) `catch` \(exception :: SomeException) -> do
            putStrLn ("opcert-live: server restart after: " <> show exception)
            threadDelay 1_000_000


emitHeaderEvidence :: ReSignParams -> IORef (Set.Set String) -> FilePath -> Header -> IO ()
emitHeaderEvidence params seenHeaders evidence header = do
    let (resigned, outcome) = reSignHeader params header
        HeaderFields sourceSlot sourceBlock sourceHash = getHeaderFields header
        HeaderFields _ _ resignedHash = getHeaderFields resigned
        details =
            object
                [ "kind" .= describeOutcome outcome
                , "source_slot" .= show sourceSlot
                , "source_block" .= show sourceBlock
                , "source_hash" .= show sourceHash
                , "resigned_hash" .= show resignedHash
                ]
        key = show sourceHash
    first <- atomicModifyIORef' seenHeaders $ \seen ->
        if Set.member key seen then (seen, False) else (Set.insert key seen, True)
    if first
        then do
            appendEvidence evidence details
            case outcome of
                ReSignedConway _ -> SDK.sometimes True "opcert_valid_control_served" details
                _ -> SDK.reachable "opcert_passthrough_served" details
        else pure ()
  where
    describeOutcome :: ReSignOutcome -> String
    describeOutcome = \case
        ReSignedConway _ -> "opcert_valid_control_resign"
        PassthroughForeignPool -> "passthrough_foreign_pool"
        PassthroughNonConway -> "passthrough_non_conway"


appendEvidence :: FilePath -> Value -> IO ()
appendEvidence path value = do
    createDirectoryIfMissing True (takeDirectory path)
    exists <- doesFileExist path
    size <- if exists then getFileSize path else pure 0
    if size >= 4 * 1024 * 1024
        then pure ()
        else withBinaryFile path AppendMode $ \handle -> LBS8.hPutStrLn handle (encode value)


-- | Serve a single opcert CASE against an isolated victim relay: relay the
-- honest advancing chain (byte-identical valid-control) to advance the victim
-- to the live tip, then inject EXACTLY ONE header for the case at the live tip
-- (valid-control serves the real header unchanged; a rule case serves a real
-- pool1 header with ONLY its opcert field mutated + re-signed), append one
-- evidence line, and keep relaying the honest chain so the relay stays alive.
runCase
    :: String -> String -> Int -> Int
    -> FilePath -> FilePath -> Word -> Word -> FilePath -> Maybe FilePath -> IO ()
runCase caseId host upstreamPort listenPort kesSkey coldSkey slots maxEvo evidence mCaseSpecPath = do
    kesKey <- loadKesSignKey kesSkey
    coldKey <- loadColdSignKey coldSkey
    (mSpec, mSpecValue) <- loadCaseSpec mCaseSpecPath
    let ks = KeySet kesKey coldKey slots maxEvo
        magic = NetworkMagic 42
        specField = maybe [] (\v -> ["spec" .= v]) mSpecValue
    chainVar <- newTVarIO Chain.Genesis
    firedRef <- newIORef False
    deviantRef <- newIORef (Nothing :: Maybe (String, LBS.ByteString))
    appendEvidence evidence $
        object (["kind" .= ("opcert_case_started" :: String), "case" .= caseId, "slots_per_kes" .= slots] ++ specField)
    _ <- forkIO $ forever $ do
        result <- runChainProducerInto chainVar magic host (fromIntegral upstreamPort)
        case result of
            Left exception -> putStrLn ("opcert-case: upstream ended: " <> show exception)
            Right () -> putStrLn "opcert-case: upstream ended cleanly"
        threadDelay 1_000_000
    let transform live h
            | not live = pure h
            | otherwise = do
                fired <- readIORef firedRef
                if fired
                    then pure h
                    else if not (caseEligible ks h)
                        then pure h
                        else case applyCaseSpec ks mSpec caseId h of
                            Left reason -> do
                                writeIORef firedRef True
                                putStrLn ("opcert-case: FINDING " <> caseId <> ": " <> reason)
                                appendEvidence evidence $
                                    object
                                        ([ "kind" .= ("opcert_case_unreachable" :: String)
                                        , "case" .= caseId
                                        , "reason" .= reason
                                        ] ++ specField)
                                pure h
                            Right cr -> do
                                writeIORef firedRef True
                                let HeaderFields (SlotNo slotW) _ hash = getHeaderFields (crHeader cr)
                                encInfo <- case mSpec >>= csEncodingForm of
                                    Just form -> do
                                        let canonical = toLazyByteString (encHeader (crHeader cr))
                                            deviant = reEncodeOpcert (maybe 0 csSeed mSpec) form (mSpec >>= csTrailingLen) canonical
                                        writeIORef deviantRef (Just (show hash, deviant))
                                        pure ["encoding_form" .= form, "deviant_bytes" .= LBS.length deviant]
                                    Nothing -> pure []
                                putStrLn
                                    ( "opcert-case: INJECT " <> caseId
                                        <> " mutation=" <> caseMutationName (crMutation cr)
                                        <> " verdict=" <> crExpectedVerdict cr
                                        <> " slot=" <> show slotW
                                        <> " hash=" <> show hash
                                    )
                                appendEvidence evidence $
                                    object
                                        ([ "kind" .= ("opcert_case_served" :: String)
                                        , "case" .= caseId
                                        , "mutation" .= caseMutationName (crMutation cr)
                                        , "header_hash" .= show hash
                                        , "slot" .= slotW
                                        , "pool" .= ("pool1" :: String)
                                        , "expected_verdict" .= crExpectedVerdict cr
                                        ] ++ encInfo ++ specField)
                                pure (crHeader cr)
        onAccept peer = putStrLn ("opcert-case: accepted " <> peer)
        server = caseInjectingChainSyncServer putStrLn transform chainVar
    forever $
        ( runChainSyncServer
            magic
            (fromIntegral listenPort)
            onAccept
            (deviantCodec deviantRef)
            server
            plainBlockFetchCodec
            (onDemandBlockFetchResponder putStrLn (const (pure ())) magic (host, upstreamPort) chainVar)
            >> pure ()
        ) `catch` \(exception :: SomeException) -> do
            putStrLn ("opcert-case: server restart after: " <> show exception)
            threadDelay 1_000_000


-- | Parse @--flag value@ pairs (order-independent) into an assoc list.
parseFlags :: [String] -> [(String, String)]
parseFlags (flag@('-':'-':_) : value : rest) = (flag, value) : parseFlags rest
parseFlags _ = []

lookupFlag :: String -> [(String, String)] -> Maybe String
lookupFlag = lookup


-- | Load the optional seed-derived case spec (soak mode). Returns the parsed
-- 'CaseSpec' (for the deterministic override) and the raw JSON 'Value' (echoed
-- verbatim into evidence so a finding row is replayable).
loadCaseSpec :: Maybe FilePath -> IO (Maybe CaseSpec, Maybe Value)
loadCaseSpec Nothing = pure (Nothing, Nothing)
loadCaseSpec (Just path) = do
    raw <- LBS.readFile path
    case parseCaseSpec raw of
        Left err -> error ("case-spec parse: " <> err)
        Right spec ->
            let mv = either (const Nothing) Just (eitherDecode raw :: Either String Value)
             in pure (Just spec, mv)

-- | A ChainSync codec that serves the seed-derived encoding-form deviant wire
-- bytes for the ONE fired case header (matched by hash), and every other header
-- canonically. The deviation lives in the IORef the case transform writes; the
-- pure encode path reads it via 'unsafePerformIO' (adversary-only). When the
-- ref is empty this is byte-identical to 'plainCodec'.
deviantCodec
    :: IORef (Maybe (String, LBS.ByteString))
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
deviantCodec ref =
    ChainSyncCodec.codecChainSync enc decHeader encPoint decPoint encTip decTip
  where
    enc h = unsafePerformIO $ do
        m <- readIORef ref
        pure $ case m of
            Just (hh, dev)
                | hh == headerHashString h -> encodePreEncoded (LBS.toStrict dev)
            _ -> encHeader h

headerHashString :: Header -> String
headerHashString h = let HeaderFields _ _ hh = getHeaderFields h in show hh
