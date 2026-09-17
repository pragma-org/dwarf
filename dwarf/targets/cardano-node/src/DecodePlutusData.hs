{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}

-- | Decode raw Conway-era Plutus data using cardano-ledger's typed decoder.
module Main (main) where

import qualified Data.ByteString.Lazy as BSL
import System.Exit (ExitCode (..), exitSuccess, exitWith)

import Cardano.Ledger.Api.Era (ConwayEra, eraProtVerLow)
import Cardano.Ledger.Binary.Decoding (DecCBOR (decCBOR), decodeFullAnnotator)
import Cardano.Ledger.Plutus.Data (Data)

main :: IO ()
main = do
  bytes <- BSL.getContents
  case decodeFullAnnotator (eraProtVerLow @ConwayEra) "PlutusData" decCBOR bytes of
    Left err -> do
      putStrLn ("ERR " ++ show err)
      exitWith (ExitFailure 1)
    Right (_ :: Data ConwayEra) -> do
      putStrLn "OK"
      exitSuccess
