{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}

module Main (main) where

import Control.DeepSeq (force)
import Control.Exception (evaluate)
import Data.Aeson (encode, object, (.=))
import Data.Aeson.Types (Pair)
import qualified Data.ByteString.Lazy as BSL
import qualified Data.ByteString.Lazy.Char8 as LBS
import Data.Word (Word8)
import GHC.Clock (getMonotonicTimeNSec)
import Numeric (showHex)
import System.Exit (ExitCode (..), exitSuccess, exitWith)

import Cardano.Ledger.Api.Era (ConwayEra, eraProtVerLow)
import Cardano.Ledger.Binary (DecCBOR (decCBOR), decodeFullAnnotator, serialize)
import Cardano.Ledger.Plutus.Data (Data)

sourceRevision :: String
sourceRevision = "fef83fed01d7926f3de83b3b917be5a4a48768b5"

hexByte :: Word8 -> String
hexByte byte = case showHex byte "" of
  [digit] -> ['0', digit]
  digits -> digits

hexLazy :: BSL.ByteString -> String
hexLazy = concatMap hexByte . BSL.unpack

emit :: [Pair] -> IO ()
emit fields = LBS.putStrLn $ encode $ object fields

main :: IO ()
main = do
  bytes <- BSL.getContents
  _ <- evaluate (BSL.length bytes)
  startedNs <- getMonotonicTimeNSec
  let decoded = decodeFullAnnotator (eraProtVerLow @ConwayEra) "PlutusData" decCBOR bytes
  case decoded of
    Left err -> do
      endedNs <- getMonotonicTimeNSec
      let elapsedNs = endedNs - startedNs
      emit
        [ "schema_version" .= ("v1" :: String)
        , "implementation" .= ("cardano-node" :: String)
        , "source_revision" .= sourceRevision
        , "boundary" .= ("production-codec-only" :: String)
        , "outcome" .= ("rejected" :: String)
        , "elapsed_nanos" .= elapsedNs
        , "elapsed_micros" .= (elapsedNs `div` 1000)
        , "error_class" .= ("decode-error" :: String)
        , "error" .= show err
        ]
      exitWith (ExitFailure 1)
    Right (value :: Data ConwayEra) -> do
      evaluated <- evaluate (force value)
      endedNs <- getMonotonicTimeNSec
      let elapsedNs = endedNs - startedNs
          first = serialize (eraProtVerLow @ConwayEra) evaluated
          decodedAgain = decodeFullAnnotator (eraProtVerLow @ConwayEra) "PlutusData" decCBOR first
      case decodedAgain of
        Left err -> do
          emit
            [ "schema_version" .= ("v1" :: String)
            , "implementation" .= ("cardano-node" :: String)
            , "source_revision" .= sourceRevision
            , "boundary" .= ("production-codec-only" :: String)
            , "outcome" .= ("accepted" :: String)
            , "elapsed_nanos" .= elapsedNs
            , "elapsed_micros" .= (elapsedNs `div` 1000)
            , "first_encode_hex" .= hexLazy first
            , "second_decode_outcome" .= ("rejected" :: String)
            , "roundtrip_error" .= show err
            ]
          exitSuccess
        Right (secondValue :: Data ConwayEra) -> do
          secondEvaluated <- evaluate (force secondValue)
          let second = serialize (eraProtVerLow @ConwayEra) secondEvaluated
          emit
            [ "schema_version" .= ("v1" :: String)
            , "implementation" .= ("cardano-node" :: String)
            , "source_revision" .= sourceRevision
            , "boundary" .= ("production-codec-only" :: String)
            , "outcome" .= ("accepted" :: String)
            , "elapsed_nanos" .= elapsedNs
            , "elapsed_micros" .= (elapsedNs `div` 1000)
            , "first_encode_hex" .= hexLazy first
            , "second_encode_hex" .= hexLazy second
            , "second_decode_outcome" .= ("accepted" :: String)
            ]
          exitSuccess
