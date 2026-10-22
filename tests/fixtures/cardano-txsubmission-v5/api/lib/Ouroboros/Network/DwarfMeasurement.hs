{-# LANGUAGE OverloadedStrings #-}

-- | Opt-in, bounded NDJSON sink for the DWARF measurement build.
--
-- The stock node never imports this module. The exact measurement target
-- enables it only when DWARF_CARDANO_MEASUREMENT_TRACE names a file.
module Ouroboros.Network.DwarfMeasurement
  ( emitDwarfProtocolMeasurement
  , measureDwarfEither
  , measureDwarfPure
  , measureDwarfHeader
  , dwarfBlockFetchEnabled
  , dwarfQueueStamp
  , emitDwarfBlockFetch
  , measureDwarfFetchedBlock
  ) where

import Control.Concurrent.MVar (MVar, newMVar, withMVar)
import Control.Exception (SomeAsyncException, SomeException, evaluate, fromException, throwIO, try)
import Control.Monad.Class.MonadThrow qualified as MC
import Control.Monad.Class.MonadTime (MonadMonotonicTimeNSec, getMonotonicTimeNSec)
import Data.Aeson (encode, object, (.=))
import qualified Data.ByteString.Char8 as BS
import qualified Data.ByteString.Lazy.Char8 as LBS
import Data.Word (Word64)
import Numeric (showHex)
import System.Random (randomIO)
import System.Environment (lookupEnv)
import System.IO (BufferMode (LineBuffering), Handle, IOMode (AppendMode),
                  hSetBuffering, openFile)
import System.IO.Unsafe (unsafePerformIO)


{-# NOINLINE measurementHandle #-}
measurementHandle :: Maybe (MVar Handle)
measurementHandle = unsafePerformIO $ do
  path <- lookupEnv "DWARF_CARDANO_MEASUREMENT_TRACE"
  case path of
    Nothing -> pure Nothing
    Just "" -> pure Nothing
    Just output -> do
      handle <- openFile output AppendMode
      hSetBuffering handle LineBuffering
      Just <$> newMVar handle


-- | Emit after the measured boundary has ended. File-lock and JSON encoding
-- overhead therefore do not contribute to @elapsed_nanos@ or @duration_us@. The boundary covers
-- receive plus incremental decode, not pure CBOR decode alone.
{-# NOINLINE emitDwarfProtocolMeasurement #-}
emitDwarfProtocolMeasurement
  :: String
  -> String
  -> String
  -> Word64
  -> Word64
  -> Word64
  -> ()
emitDwarfProtocolMeasurement protocol state outcome endedNs elapsedNs durationUs =
  case measurementHandle of
    Nothing -> ()
    Just lock -> unsafePerformIO $ withMVar lock $ \handle -> do
      LBS.hPutStrLn handle $ encode $ object
        [ "schema_version" .= ("v1" :: String)
        , "target" .= ("cardano-node::measurement" :: String)
        , "event" .= ("protocol_receive_decode" :: String)
        , "boundary" .= ("receive-plus-incremental-decode" :: String)
        , "protocol" .= protocol
        , "state" .= state
        , "outcome" .= outcome
        , "ended_monotonic_ns" .= endedNs
        , "elapsed_nanos" .= elapsedNs
        , "duration_us" .= durationUs
        ]


emitDwarfStageMeasurementIO :: String -> String -> Word64 -> Word64 -> Word64 -> IO ()
emitDwarfStageMeasurementIO stage outcome endedNs elapsedNs durationUs =
  case measurementHandle of
    Nothing -> pure ()
    Just lock -> withMVar lock $ \handle ->
      LBS.hPutStrLn handle $ encode $ object
        [ "schema_version" .= ("v1" :: String)
        , "target" .= ("cardano-node::measurement" :: String)
        , "event" .= ("ledger_stage" :: String)
        , "stage" .= stage
        , "outcome" .= outcome
        , "ended_monotonic_ns" .= endedNs
        , "elapsed_nanos" .= elapsedNs
        , "duration_us" .= durationUs
        ]


{-# NOINLINE measureDwarfPure #-}
measureDwarfPure :: String -> a -> a
measureDwarfPure stage value = unsafePerformIO $ do
  startedNs <- getMonotonicTimeNSec
  result <- evaluate value
  endedNs <- getMonotonicTimeNSec
  emitDwarfStageMeasurementIO stage "completed" endedNs
    (endedNs - startedNs)
    ((endedNs - startedNs) `div` 1000)
  pure result


{-# NOINLINE measureDwarfEither #-}
measureDwarfEither :: String -> Either e a -> Either e a
measureDwarfEither stage value = unsafePerformIO $ do
  startedNs <- getMonotonicTimeNSec
  result <- evaluate value
  outcome <- case result of
    Left _ -> pure "rejected"
    Right payload -> evaluate payload >> pure "accepted"
  endedNs <- getMonotonicTimeNSec
  emitDwarfStageMeasurementIO stage outcome endedNs
    (endedNs - startedNs)
    ((endedNs - startedNs) `div` 1000)
  pure result


-- | Only the outer Either constructor is evaluated, as the native caller does.
-- Never evaluate or show a successful payload. Disabled tracing returns the
-- original thunk. Keep native evaluation separate from all instrumentation:
-- diagnostic, clock, encoding and sink failures cannot replace its result.
{-# NOINLINE measureDwarfHeader #-}
measureDwarfHeader :: String -> (e -> String) -> (e -> String) -> Either e a -> Either e a
measureDwarfHeader headerHash classifyError renderError value = unsafePerformIO $ do
  sink <- tryHeaderInstrumentation (evaluate measurementHandle)
  case sink of
    Left _ -> pure value
    Right Nothing -> pure value
    Right (Just lock) -> do
      started <- tryHeaderInstrumentation getMonotonicTimeNSec
      evaluated <- try (evaluate value)
      -- Native cancellation must leave before any further instrumentation.
      case evaluated of
        Left failure -> rethrowHeaderAsync failure
        Right _ -> pure ()
      emitted <- tryHeaderInstrumentation $ do
        startedNs <- either throwIO pure started
        endedNs <- getMonotonicTimeNSec
        let elapsedNs = endedNs - startedNs
            (outcome, errorKind, reason) = case evaluated of
              Right (Right _) -> ("accepted", "none", "")
              Right (Left err) -> ("rejected", classifyError err, renderError err)
              Left _ -> ("unavailable", "evaluation-exception", "")
            -- Public hash only: never serialize a header or successful state.
            boundedHash = take 65 headerHash
            boundedReason = map sanitize (take 1024 reason)
            sanitize c | c >= ' ' && c <= '~' = c
                       | otherwise = '?'
        -- Force the entire JSON before writing, inside the instrumentation
        -- guard. This also contains every lazy classification/render thunk.
        encoded <- evaluate $ LBS.toStrict $ encode $ object
          [ "schema_version" .= ("v1" :: String)
          , "target" .= ("cardano-node::measurement" :: String)
          , "event" .= ("header_validation" :: String)
          , "boundary" .= ("consensus-validate-header" :: String)
          , "header_hash" .= boundedHash
          , "outcome" .= (outcome :: String)
          , "error_kind" .= errorKind
          , "reason" .= boundedReason
          , "ended_monotonic_ns" .= endedNs
          , "elapsed_nanos" .= elapsedNs
          , "duration_us" .= (elapsedNs `div` 1000)
          ]
        withMVar lock $ \handle -> BS.hPutStrLn handle encoded
      case emitted of
        Left _ -> emitHeaderUnavailable lock
        Right () -> pure ()
      -- Preserve the original synchronous native exception after telemetry.
      case evaluated of
        Left failure -> throwIO (failure :: SomeException)
        Right result -> pure result


tryHeaderInstrumentation :: IO a -> IO (Either SomeException a)
tryHeaderInstrumentation action = do
  result <- try action
  case result of
    Left failure -> rethrowHeaderAsync failure >> pure result
    Right _ -> pure result


rethrowHeaderAsync :: SomeException -> IO ()
rethrowHeaderAsync failure =
  -- SomeAsyncException includes ThreadKilled, UserInterrupt and custom async
  -- exceptions. Do not render it or attempt a second, potentially blocking IO.
  case fromException failure :: Maybe SomeAsyncException of
    Just _ -> throwIO failure
    Nothing -> pure ()


-- Constant, bounded fallback: no header/diagnostic thunk, no timing fabrication.
-- Synchronous write failures preserve native semantics; async cancels at once.
emitHeaderUnavailable :: MVar Handle -> IO ()
emitHeaderUnavailable lock = do
  _ <- tryHeaderInstrumentation $ withMVar lock $ \handle ->
    BS.hPutStrLn handle
      "{\"schema_version\":\"v1\",\"target\":\"cardano-node::measurement\",\"event\":\"header_validation\",\"boundary\":\"consensus-validate-header\",\"outcome\":\"unavailable\",\"error_kind\":\"instrumentation-exception\"}"
  pure ()


-- A process-local random 128-bit trace namespace separates restarted nodes.
-- Its construction/forcing is contained by the diagnostic encoding guard.
{-# NOINLINE dwarfTraceSession #-}
dwarfTraceSession :: String
dwarfTraceSession = unsafePerformIO $ do
  a <- randomIO :: IO Word64
  b <- randomIO :: IO Word64
  let hex n = let s = showHex n "" in replicate (16 - length s) '0' ++ s
  pure (hex a ++ hex b)


-- | Force this flag before entering STM: opening a diagnostic sink is IO.
{-# NOINLINE dwarfBlockFetchEnabled #-}
dwarfBlockFetchEnabled :: Bool
dwarfBlockFetchEnabled = unsafePerformIO $ do
  sink <- tryHeaderInstrumentation (evaluate measurementHandle)
  pure $ case sink of
    Right (Just _) -> True
    _ -> False


-- | Clock IO stays outside retried STM. Synchronous diagnostic failure cannot
-- change native progress; SomeAsyncException always cancels immediately.
dwarfQueueStamp :: (MC.MonadCatch m, MonadMonotonicTimeNSec m) => m (Maybe Word64)
dwarfQueueStamp =
  if dwarfBlockFetchEnabled
    then MC.catch (Just <$> getMonotonicTimeNSec) $ \failure ->
      case fromException failure :: Maybe SomeAsyncException of
        Just _ -> MC.throwIO (failure :: SomeException)
        Nothing -> pure Nothing
    else pure Nothing


-- | Only bounded metadata enters the sink; never serialize a block or payload.
-- Both clock endpoints bracket native calls, not STM commit instants.
{-# NOINLINE emitDwarfBlockFetch #-}
emitDwarfBlockFetch :: String -> Word64 -> Word64 -> Word64 -> Int
                   -> Maybe Word64 -> Maybe Word64 -> String -> ()
emitDwarfBlockFetch phase clientId requestId updateIndex depth started ended blockHash =
  unsafePerformIO $ do
    sink <- tryHeaderInstrumentation (evaluate measurementHandle)
    case sink of
      Right (Just lock) -> do
        emitted <- tryHeaderInstrumentation $ do
          (start, end) <- case (started, ended) of
            (Just a, Just b) | a <= b -> pure (a, b)
            _ -> ioError (userError "unavailable clock")
          let boundedHash = take 65 blockHash
          if clientId == 0 || requestId == 0 || updateIndex == 0
              || depth < 0 || depth > 65536
            then ioError (userError "unavailable queue metadata")
            else pure ()
          encoded <- evaluate $ LBS.toStrict $ encode $ object
            [ "schema_version" .= ("v1" :: String)
            , "target" .= ("cardano-node::measurement" :: String)
            , "event" .= ("blockfetch_handler_queue" :: String)
            , "boundary" .= ("client-merged-request" :: String)
            , "phase" .= phase
            , "trace_session" .= dwarfTraceSession
            , "client_id" .= clientId
            , "request_id" .= requestId
            , "update_index" .= updateIndex
            , "queue_depth" .= depth
            , "started_monotonic_ns" .= start
            , "ended_monotonic_ns" .= end
            , "block_hash" .= boundedHash
            ]
          withMVar lock $ \handle -> BS.hPutStrLn handle encoded
        case emitted of
          Right () -> pure ()
          Left _ -> do
            _ <- tryHeaderInstrumentation $ withMVar lock $ \handle ->
              BS.hPutStrLn handle
                "{\"schema_version\":\"v1\",\"target\":\"cardano-node::measurement\",\"event\":\"blockfetch_handler_queue\",\"boundary\":\"client-merged-request\",\"phase\":\"unavailable\"}"
            pure ()
      _ -> pure ()


-- | The native addFetchedBlock action is deliberately outside every telemetry
-- catch. Preserve its result and exceptions without inspecting either payload.
measureDwarfFetchedBlock :: (MC.MonadCatch m, MonadMonotonicTimeNSec m)
                        => Word64 -> Maybe (Word64, Word64) -> String -> m a -> m a
measureDwarfFetchedBlock clientId pending blockHash action = do
  started <- dwarfQueueStamp
  native <- action
  ended <- dwarfQueueStamp
  case pending of
    Just (requestId, updateIndex) ->
      emitDwarfBlockFetch "service" clientId requestId updateIndex 0
        started ended blockHash `seq` pure native
    Nothing -> pure native
