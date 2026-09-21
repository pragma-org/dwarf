{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE RecordWildCards #-}

module Main where

import Control.Monad.Except (runExcept)
import Control.Monad.Writer.Strict (runWriterT)
import Codec.CBOR.Decoding (decodeBytes)
import Codec.CBOR.Read (deserialiseFromBytes)
import Data.Aeson
import Data.Bits ((.|.), shiftL)
import Data.ByteString qualified as BS
import Data.ByteString.Lazy.Char8 qualified as BSL
import Data.ByteString.Short qualified as SBS
import Data.Char (digitToInt, isHexDigit)
import Data.Int (Int64)
import GHC.Clock (getMonotonicTimeNSec)
import PlutusCore.Evaluation.Machine.ExBudget (ExBudget (..))
import PlutusLedgerApi.Common qualified as Common
import PlutusLedgerApi.Common.Versions (MajorProtocolVersion (..))
import PlutusLedgerApi.V2 qualified as V2
import System.Exit (ExitCode (..), exitWith)
import UntypedPlutusCore.Evaluation.Machine.Cek qualified as UPLC

data Request = Request
  { costModel :: [Int64]
  , costModelSha256 :: String
  , protocolVersion :: Int
  , plutusVersion :: String
  , schemaVersion :: String
  , scriptCborHex :: String
  }

instance FromJSON Request where
  parseJSON = withObject "Request" $ \o ->
    Request <$> o .: "cost_model" <*> o .: "cost_model_sha256"
      <*> o .: "protocol_version" <*> o .: "plutus_version"
      <*> o .: "schema_version" <*> o .: "script_cbor_hex"

hexBytes :: String -> Either String BS.ByteString
hexBytes value
  | odd (length value) || any (not . isHexDigit) value = Left "invalid script_cbor_hex"
  | otherwise = Right . BS.pack $ hexPairs value
 where
  hexPairs [] = []
  hexPairs (a:b:rest) = fromIntegral ((digitToInt a `shiftL` 4) .|. digitToInt b) : hexPairs rest
  hexPairs _ = []

main :: IO ()
main = do
  input <- BSL.getContents
  request@Request{..} <- either fail pure (eitherDecode input)
  if schemaVersion /= "v1" || plutusVersion /= "v2" || protocolVersion /= 10
    then fail "unsupported request identity"
    else pure ()
  bytes <- either fail pure (hexBytes scriptCborHex)
  scriptBytes <- case deserialiseFromBytes decodeBytes (BSL.fromStrict bytes) of
    Left err -> fail (show err)
    Right (remaining, value)
      | BSL.null remaining -> pure value
      | otherwise -> fail "script envelope has trailing bytes"
  script <- either (fail . show) pure $
    V2.deserialiseScript (MajorProtocolVersion 10) (SBS.toShort scriptBytes)
  context <- either (fail . show) (pure . fst) $
    runWriterT (V2.mkEvaluationContext costModel)
  term <- either (fail . show) pure $ runExcept $
    Common.mkTermToEvaluate Common.PlutusV2 (MajorProtocolVersion 10) script
      [Common.Constr 0 [], Common.Constr 0 [], Common.Constr 0 []]
  request `seq` script `seq` context `seq` term `seq` pure ()

  before <- getMonotonicTimeNSec
  let UPLC.CekReport result (UPLC.CountingSt (ExBudget cpu memory)) _ =
        Common.evaluateTerm UPLC.counting (MajorProtocolVersion 10) Common.Quiet context term
  result `seq` cpu `seq` memory `seq` pure ()
  after <- getMonotonicTimeNSec

  let rejected = case result of
        UPLC.CekFailure _ -> True
        _ -> False
      nanos = after - before
      response = object
        [ "schema_version" .= String "v1"
        , "implementation" .= String "cardano-node"
        , "source_revision" .= String "fef83fed01d7926f3de83b3b917be5a4a48768b5"
        , "boundary" .= String "production-plutus-v2-vm-only"
        , "plutus_version" .= String "v2"
        , "cost_model_sha256" .= costModelSha256
        , "outcome" .= String (if rejected then "rejected" else "accepted")
        , "cpu_budget" .= cpu
        , "memory_budget" .= memory
        , "elapsed_nanos" .= nanos
        , "elapsed_micros" .= (nanos `div` 1000)
        ]
  BSL.putStrLn (encode response)
  if rejected then exitWith (ExitFailure 1) else pure ()
