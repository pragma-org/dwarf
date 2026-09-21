{-# LANGUAGE DataKinds #-}
{-# LANGUAGE ScopedTypeVariables #-}

-- | One-input coverage harness for the exact production N2N handshake codec.
module Main (main) where

import qualified Codec.CBOR.Term as CBOR
import qualified Data.ByteString.Lazy as LBS
import Network.TypedProtocol.Codec (CodecF (..), runDecoder)
import Network.TypedProtocol.Core (StateToken, StateTokenI (stateToken))
import Cardano.Network.NodeToNode.Version (NodeToNodeVersion)
import Cardano.Network.Protocol.Handshake.Codec (nodeToNodeHandshakeCodec)
import Ouroboros.Network.Protocol.Handshake.Type (Handshake (StConfirm))
import System.Environment (getArgs)
import System.Exit (exitFailure, exitSuccess)


main :: IO ()
main = do
  arguments <- getArgs
  bytes <- case arguments of
    path : _ -> LBS.readFile path
    [] -> LBS.getContents
  let Codec {decode} = nodeToNodeHandshakeCodec
  step <- decode
    (stateToken :: StateToken (StConfirm :: Handshake NodeToNodeVersion CBOR.Term))
  result <- runDecoder [bytes] step
  case result of
    Right _ -> exitSuccess
    Left _ -> exitFailure
