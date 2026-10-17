{-# LANGUAGE OverloadedStrings #-}

module Main (main) where

import Data.Aeson qualified as Aeson
import Data.ByteString qualified as BS
import Data.ByteString.Lazy qualified as LBS
import DwarfAdversary.LocalTxSubmission.Raw
    ( decodeSubmissionRequest
    , encodeSubmissionReceipt
    , submitRaw
    )
import System.Exit (exitFailure)
import System.IO (hFlush, hIsEOF, stdin, stdout)

maximumRequestBytes :: Int
maximumRequestBytes = 262144

main :: IO ()
main = do
    request <- readBounded maximumRequestBytes
    case request >>= decodeSubmissionRequest of
        Left message -> do
            LBS.hPut stdout $ "{\"schema_version\":1,\"outcome\":\"identity_refused\",\"detail\":"
                <> encodeJsonString message <> "}\n"
            hFlush stdout
            exitFailure
        Right (binding, payload, transactionEra, timeoutSeconds) -> do
            receipt <- submitRaw binding payload transactionEra timeoutSeconds
            LBS.hPut stdout (encodeSubmissionReceipt receipt <> "\n")
            hFlush stdout

readBounded :: Int -> IO (Either String BS.ByteString)
readBounded limit = go [] 0
  where
    go chunks size = do
        chunk <- BS.hGetSome stdin (min 4096 (limit + 1 - size))
        if BS.null chunk
            then pure (Right (BS.concat (reverse chunks)))
            else do
                let size' = size + BS.length chunk
                if size' > limit
                    then pure (Left "request exceeds 262144 bytes")
                    else do
                        eof <- hIsEOF stdin
                        if eof
                            then pure (Right (BS.concat (reverse (chunk : chunks))))
                            else go (chunk : chunks) size'

encodeJsonString :: String -> LBS.ByteString
encodeJsonString = Aeson.encode
