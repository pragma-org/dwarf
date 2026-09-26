{-# LANGUAGE DataKinds #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE NumericUnderscores #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}

module Main (main) where

import Codec.CBOR.Write (toLazyByteString)
import Codec.CBOR.Term (decodeTerm, encodeTerm)
import Codec.CBOR.Read (deserialiseFromBytes)
import Control.Concurrent (forkIO, threadDelay)
import Control.Concurrent.Class.MonadSTM.Strict (newTVarIO)
import Control.Exception (SomeException, catch)
import Control.Monad (forM_, forever)
import Data.Aeson (Value, eitherDecode, encode, object, (.=))
import Data.Aeson qualified as A
import Data.Aeson.KeyMap qualified as AKM
import Data.ByteString.Lazy qualified as LBS
import Data.ByteString.Lazy.Char8 qualified as LBS8
import Data.IORef (IORef, atomicModifyIORef', modifyIORef', newIORef, readIORef, writeIORef)
import Data.Map.Strict (Map)
import Data.Map.Strict qualified as Map
import Data.Text qualified as T
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
    , caseSpecByteSeed
    , reEncodeOpcert
    , mutateFirstOpcert
    , mutateOpcertBytes
    , caseEligible
    , caseMutationName
    , loadColdSignKey
    , loadKesSignKey
    , plainCodec
    , reSignHeader
    , resignCodec
    )
import Cardano.Slotting.Slot (SlotNo (..))
import Cardano.Ledger.Binary (DecCBOR (decCBOR), DecoderError, decodeFullAnnotator)
import Cardano.Ledger.Binary.Version (natVersion)
import Cardano.Protocol.Crypto (StandardCrypto)
import Cardano.Protocol.Praos.BlockHeader qualified as Praos
import System.Exit (ExitCode (ExitFailure), exitWith)
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
import System.FilePath (takeDirectory, (</>))
import System.IO (BufferMode (LineBuffering), IOMode (AppendMode), hPutStrLn, hSetBuffering, stderr, stdin, stdout, withBinaryFile)
import Text.Printf (printf)
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
        ["decode-praos-header"] -> runDecodeHeader
        ["mutate-opcert", mut] -> runMutateOpcert mut
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
                            caseSpecDir = lookupFlag "--case-spec-dir" flags
                         in runCase caseId host upstreamPort listenPort kesSkey coldSkey slots maxEvo evidence caseSpecPath caseSpecDir
                    _ -> usage
        _ -> usage


-- | Decoder-differential mode (family #6): read a bare Praos block-header CBOR
-- from stdin and report whether cardano-node's ledger decoder accepts it. The
-- contract matches the standalone decode binaries (amaru-cbor-decode-block-header
-- etc.): stdout "OK" + exit 0 on a clean decode, "ERR <msg>" + exit 1 on a clean
-- decode error. Feeding the SAME malformed-opcert header CBOR here and to the
-- amaru BlockHeader decoder yields the cross-decoder differential.
-- | Corpus generation for family #6: read a valid base Praos header CBOR from
-- stdin, apply a named opcert-field CBOR mutation to its opcert sub-structure,
-- and write the mutated header bytes to stdout. Only the opcert region changes;
-- every other header byte is preserved (Term round-trip), so a decoder\'s
-- accept/reject is attributable to the opcert mutation alone.
runMutateOpcert :: String -> IO ()
runMutateOpcert mut = do
    lbs <- LBS.hGetContents stdin
    case deserialiseFromBytes decodeTerm lbs of
        Left err -> do
            hPutStrLn stderr ("mutate-opcert: base decode failed: " <> show err)
            exitWith (ExitFailure 2)
        Right (_, term) ->
            LBS.putStr (toLazyByteString (encodeTerm (mutateFirstOpcert mut term)))


runDecodeHeader :: IO ()
runDecodeHeader = do
    lbs <- LBS.hGetContents stdin
    case decodeFullAnnotator (natVersion @9) "Header" decCBOR lbs
            :: Either DecoderError (Praos.Header StandardCrypto) of
        Right _ -> putStrLn "OK"
        Left err -> do
            putStrLn ("ERR " <> show err)
            exitWith (ExitFailure 1)


usage :: IO a
usage =
    error
        "usage:\n\
        \  dwarf-opcert-adversary verify-resign HOST PORT COUNT KES_SKEY SLOTS_PER_KES\n\
        \  dwarf-opcert-adversary live-proxy --upstream HOST:PORT --listen-port PORT --kes-skey FILE --slots-per-kes N --evidence FILE\n\
        \  dwarf-opcert-adversary serve-case --case CASE_ID --upstream HOST:PORT --listen-port PORT --kes-skey FILE --cold-skey FILE --slots-per-kes N --max-kes-evo N --evidence FILE [--case-spec FILE] [--case-spec-dir DIR]"


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


-- | Derive a foreign pool's @cold.skey@ path from our own @--cold-skey@ path.
-- Our cold key lives at @.../pools-keys/<ourPool>/cold.skey@; the foreign pool's
-- is the sibling @.../pools-keys/<foreignPool>/cold.skey@.
foreignColdPath :: FilePath -> String -> FilePath
foreignColdPath ourCold foreignPool =
    takeDirectory (takeDirectory ourCold) </> foreignPool </> "cold.skey"

-- | Load a foreign pool's cold signing key when a foreign pool is named, else
-- 'Nothing'. Fails loudly (fail-closed) if the named pool's key is absent.
loadForeignColdMaybe _ Nothing = pure Nothing
loadForeignColdMaybe ourCold (Just foreignPool) = do
    let path = foreignColdPath ourCold foreignPool
    exists <- doesFileExist path
    if exists
        then Just <$> loadColdSignKey path
        else error ("cross-pool: foreign cold key not found: " <> path)

-- | The effective KeySet for a given served spec: for the cross-pool family the
-- per-index spec's @foreign_pool@ selects which pool's cold key signs the
-- opcert, so (in persistent mode, where many specs stream through one forger)
-- the foreign key must track the current spec rather than the one loaded at
-- startup. When the spec names the same (or no) foreign pool the base KeySet is
-- reused unchanged.
ksForSpec :: KeySet -> FilePath -> Maybe CaseSpec -> IO KeySet
ksForSpec base _ Nothing = pure base
ksForSpec base ourCold (Just spec) = case csForeignPool spec of
    Nothing -> pure base
    Just _  -> do
        mFc <- loadForeignColdMaybe ourCold (csForeignPool spec)
        pure base { ksForeignColdSignKey = mFc }


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
-- When @mCaseSpecDir@ is given the forger runs in PERSISTENT multi-serve mode
-- (soak): it stays connected, follows the tip, and at EVERY one of its pool's
-- live leader-slot headers it consumes the next @spec-<n>.json@ from the dir,
-- serves that seed-derived case, emits one @opcert_case_served@ line tagged with
-- its @served_index@, and re-arms for the next leader slot. This converts the
-- serve throughput to the pool's natural leader rate (many cases per single
-- long-lived connection) instead of one case per reconnect. When absent the
-- forger keeps its original one-shot behaviour (fixed + single-spec scenarios).
runCase
    :: String -> String -> Int -> Int
    -> FilePath -> FilePath -> Word -> Word -> FilePath -> Maybe FilePath -> Maybe FilePath -> IO ()
runCase caseId host upstreamPort listenPort kesSkey coldSkey slots maxEvo evidence mCaseSpecPath mCaseSpecDir = do
    kesKey <- loadKesSignKey kesSkey
    coldKey <- loadColdSignKey coldSkey
    (mSpec, mSpecValue) <- loadCaseSpec mCaseSpecPath
    -- Cross-pool family: the case-spec (single-spec mode) may name a foreign
    -- pool whose cold key authorizes the opcert. Derive its cold.skey from the
    -- sibling directory of our own --cold-skey (.../pools-keys/<pool>/cold.skey)
    -- so no extra flag/driver change is needed. In persistent (spec-dir) mode
    -- the per-index spec supplies foreign_pool; we then load it lazily below via
    -- the same derivation keyed on that spec (handled in serveCaseOn).
    mForeignCold <- loadForeignColdMaybe coldSkey (mSpec >>= csForeignPool)
    let ks = KeySet kesKey coldKey slots maxEvo mForeignCold
        magic = NetworkMagic 42
        specField = maybe [] (\v -> ["spec" .= v]) mSpecValue
        persistent = maybe False (const True) mCaseSpecDir
    chainVar <- newTVarIO Chain.Genesis
    firedRef <- newIORef False
    nextIxRef <- newIORef (0 :: Int)
    deviants <- newIORef (Map.empty :: Map String LBS.ByteString)
    appendEvidence evidence $
        object (["kind" .= ("opcert_case_started" :: String), "case" .= caseId
                , "slots_per_kes" .= slots, "persistent" .= persistent]
               ++ maybe [] (\d -> ["case_spec_dir" .= d]) mCaseSpecDir ++ specField)
    _ <- forkIO $ forever $ do
        result <- runChainProducerInto chainVar magic host (fromIntegral upstreamPort)
        case result of
            Left exception -> putStrLn ("opcert-case: upstream ended: " <> show exception)
            Right () -> putStrLn "opcert-case: upstream ended cleanly"
        threadDelay 1_000_000
    -- Serve ONE case (spec-driven override or fixed caseId) against header @h@:
    -- apply the opcert mutation, register the encoding-form deviant bytes (keyed
    -- by the served header hash so the codec serves them for exactly that
    -- header), emit one served/unreachable evidence line (with @extra@ fields),
    -- and return the header to roll forward.
    let serveCaseOn mSp mv cid extra h = do
          ksEff <- ksForSpec ks coldSkey mSp
          case applyCaseSpec ksEff mSp (maybe cid csBaseCase mSp) h of
            Left reason -> do
                    putStrLn ("opcert-case: FINDING " <> cid <> ": " <> reason)
                    appendEvidence evidence $
                        object ([ "kind" .= ("opcert_case_unreachable" :: String)
                                , "case" .= cid, "reason" .= reason ] ++ extra ++ specOf mv)
                    pure h
            Right cr -> do
                    let HeaderFields (SlotNo slotW) _ hash = getHeaderFields (crHeader cr)
                    encInfo <- case (mSp >>= csEncodingForm, mSp >>= csOpcertField) of
                        (Just form, _) -> do
                            let canonical = toLazyByteString (encHeader (crHeader cr))
                                deviant = reEncodeOpcert (maybe 0 caseSpecByteSeed mSp) form (mSp >>= csTrailingLen) canonical
                            modifyIORef' deviants (pruneInsert (show hash) deviant)
                            pure ["encoding_form" .= form, "deviant_bytes" .= LBS.length deviant]
                        (Nothing, Just ofield) -> do
                            let canonical = toLazyByteString (encHeader (crHeader cr))
                                deviant = mutateOpcertBytes ofield canonical
                            modifyIORef' deviants (pruneInsert (show hash) deviant)
                            pure ["opcert_field" .= ofield, "deviant_bytes" .= LBS.length deviant]
                        _ -> pure []
                    putStrLn
                        ( "opcert-case: INJECT " <> cid
                            <> " mutation=" <> caseMutationName (crMutation cr)
                            <> " verdict=" <> crExpectedVerdict cr
                            <> " slot=" <> show slotW <> " hash=" <> show hash )
                    appendEvidence evidence $
                        object ([ "kind" .= ("opcert_case_served" :: String), "case" .= cid
                                , "mutation" .= caseMutationName (crMutation cr)
                                , "header_hash" .= show hash, "slot" .= slotW
                                , "pool" .= ("pool1" :: String)
                                , "expected_verdict" .= crExpectedVerdict cr
                                ] ++ encInfo ++ extra ++ specOf mv)
                    pure (crHeader cr)
        specOf mv = maybe [] (\v -> ["spec" .= v]) mv
        -- One-shot transform: fire exactly one case at the first eligible live
        -- header, then pass everything through (fixed + single-spec scenarios).
        oneShotTransform live h
            | not live = pure h
            | otherwise = do
                fired <- readIORef firedRef
                if fired || not (caseEligible ks h)
                    then pure h
                    else do
                        writeIORef firedRef True
                        serveCaseOn mSpec mSpecValue caseId [] h
        -- Persistent transform: at each eligible live header consume the next
        -- spec-<n>.json and serve it, tagging the served line with served_index.
        persistentTransform dir live h
            | not live = pure h
            | not (caseEligible ks h) = pure h
            | otherwise = do
                ix <- readIORef nextIxRef
                let f = dir </> (printf "spec-%06d.json" ix :: String)
                exists <- doesFileExist f
                if not exists
                    then pure h  -- next case not written yet; wait for a later leader slot
                    else do
                        raw <- LBS.readFile f
                        case parseCaseSpec raw of
                            Left _ -> do
                                -- malformed: skip this index so we never wedge
                                modifyIORef' nextIxRef (+ 1)
                                pure h
                            Right spec -> do
                                let mv = either (const Nothing) Just (eitherDecode raw :: Either String Value)
                                    cid = maybe (printf "case-%06d" ix :: String) id (mv >>= caseIdFromValue)
                                modifyIORef' nextIxRef (+ 1)
                                serveCaseOn (Just spec) mv cid ["served_index" .= ix] h
        transform = maybe oneShotTransform persistentTransform mCaseSpecDir
        onAccept peer = putStrLn ("opcert-case: accepted " <> peer)
        server = caseInjectingChainSyncServer putStrLn transform chainVar
    forever $
        ( runChainSyncServer
            magic
            (fromIntegral listenPort)
            onAccept
            (deviantCodec deviants)
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
-- bytes for any fired case header (matched by hash), and every other header
-- canonically. The deviations live in the IORef map the case transform writes
-- (one entry per served case header — persistent mode serves many); the pure
-- encode path reads it via 'unsafePerformIO' (adversary-only). When the map has
-- no entry for a header this is byte-identical to 'plainCodec'.
--
-- SERVE-ONCE: the deviant bytes are popped from the map the first time that
-- header is encoded, so the ONE intended injection reaches the victim once and
-- any later re-serve of the same header (e.g. after the victim rejects the
-- malformed bytes, disconnects and re-syncs) is canonical. Without this, a
-- rejected deviant would be re-served on every reconnect, wedging the victim in
-- a reconnect loop that never advances past the poisoned header (persistent
-- mode's long-lived connection makes that loop permanent).
deviantCodec
    :: IORef (Map String LBS.ByteString)
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
deviantCodec ref =
    ChainSyncCodec.codecChainSync enc decHeader encPoint decPoint encTip decTip
  where
    enc h = unsafePerformIO $ do
        let key = headerHashString h
        m <- atomicModifyIORef' ref $ \mp ->
            case Map.lookup key mp of
                Just dev -> (Map.delete key mp, Just dev)
                Nothing  -> (mp, Nothing)
        pure $ case m of
            Just dev -> encodePreEncoded (LBS.toStrict dev)
            Nothing  -> encHeader h

-- | Insert a deviant header's bytes, keeping the map bounded (the currently
-- served header is always the freshly inserted one, so a bound never drops a
-- header we are about to encode).
pruneInsert :: String -> LBS.ByteString -> Map String LBS.ByteString -> Map String LBS.ByteString
pruneInsert k v m =
    let m' = Map.insert k v m
    in if Map.size m' > 512 then Map.deleteMin m' else m'

-- | Extract the deterministic @case_id@ from a seed-derived spec's raw JSON, so
-- a persistent-mode served line carries the same case id the driver generated.
caseIdFromValue :: Value -> Maybe String
caseIdFromValue v = case v of
    A.Object o -> case AKM.lookup "case_id" o of
        Just (A.String t) -> Just (T.unpack t)
        _ -> Nothing
    _ -> Nothing

headerHashString :: Header -> String
headerHashString h = let HeaderFields _ _ hh = getHeaderFields h in show hh
