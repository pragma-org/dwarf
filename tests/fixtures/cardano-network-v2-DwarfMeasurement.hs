{-# LANGUAGE OverloadedStrings #-}

-- | Opt-in, bounded NDJSON sink for the DWARF measurement build.
--
-- The stock node never imports this module. The exact measurement target
-- enables it only when DWARF_CARDANO_MEASUREMENT_TRACE names a file.
module Ouroboros.Network.DwarfMeasurement
  ( emitDwarfProtocolMeasurement
  , measureDwarfEither
  , measureDwarfPure
  ) where

import Control.Concurrent.MVar (MVar, newMVar, withMVar)
import Control.Exception (evaluate)
import Data.Aeson (encode, object, (.=))
import qualified Data.ByteString.Lazy.Char8 as LBS
import Data.Word (Word64)
import GHC.Clock (getMonotonicTimeNSec)
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
