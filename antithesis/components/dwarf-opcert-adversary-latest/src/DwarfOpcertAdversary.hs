{-# LANGUAGE PatternSynonyms #-}
{-# LANGUAGE LambdaCase #-}
{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE ScopedTypeVariables #-}
{-# LANGUAGE TypeApplications #-}

-- | Operational-certificate header adversary.
--
-- Unlike the KES adversary (which bit-flips the KES signature on the wire),
-- this component works on the /typed/ header: it projects a live Cardano
-- hard-fork header down to its current-era (Conway) Praos header, and
-- re-signs the header body with a devnet pool's own KES signing key.
--
-- Checkpoint 1 implements only the @valid-control@ case: re-sign the decoded
-- header's OWN 'HeaderBody' UNCHANGED at the header's current KES period. A
-- correct re-sign reproduces a byte-identical, valid header, so an isolated
-- node ADOPTS it. This proves the live-tip re-signing pipeline works before
-- the six real mutations are layered on top.
module DwarfOpcertAdversary
    ( ReSignParams (..)
    , ReSignOutcome (..)
    , loadKesSignKey
    , kesHotVerKeyBytes
    , reSignHeader
    , reSignOrPassthrough
    , resignCodec
    , singleTargetServer
    ) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.Serialise (DeserialiseFailure)
import Control.Concurrent (threadDelay)
import Data.Aeson (eitherDecodeFileStrict, (.:))
import Data.Aeson.Types (parseEither, withObject)
import Data.ByteString qualified as BS
import Data.ByteString.Base16 qualified as Base16
import Data.ByteString.Lazy qualified as LBS
import Data.List (find)
import Data.Text (Text)
import Data.Text.Encoding (encodeUtf8)

import Cardano.Crypto.KES qualified as KES
import Cardano.Protocol.Crypto (KES, StandardCrypto)
import Cardano.Protocol.Praos.BlockHeader qualified as Praos
import Cardano.Protocol.TPraos.OCert (OCert (..))
import Cardano.Slotting.Slot (SlotNo (..))

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
import Network.TypedProtocol.Codec (Codec)
import Ouroboros.Consensus.Cardano.Block (pattern HeaderConway)
import Ouroboros.Consensus.Shelley.Ledger (mkShelleyHeader, shelleyHeaderRaw)
import Ouroboros.Network.Protocol.ChainSync.Codec qualified as ChainSync
import Ouroboros.Network.Protocol.ChainSync.Server
    ( ChainSyncServer (..)
    , ServerStIdle (..)
    , ServerStIntersect (..)
    , ServerStNext (..)
    )
import Ouroboros.Network.Protocol.ChainSync.Type (ChainSync)


-- | Everything needed to re-sign a Conway Praos header for a single pool.
data ReSignParams = ReSignParams
    { rspKesSignKey :: !(KES.UnsoundPureSignKeyKES (KES StandardCrypto))
    -- ^ The pool's KES signing key at period 0 (as stored on disk). The
    -- unsound-pure primitive evolves it internally to the target period.
    , rspSlotsPerKESPeriod :: !Word
    -- ^ @slotsPerKESPeriod@ from shelley-genesis; maps slot -> KES period.
    }


-- | The result of applying a re-sign to a single header.
data ReSignOutcome
    = ReSignedConway !Word
    -- ^ A Conway header whose OCert hot key matches ours was re-signed at the
    -- given KES period.
    | PassthroughForeignPool
    -- ^ A Conway header from a different pool (hot key mismatch); left intact.
    | PassthroughNonConway
    -- ^ Not a Conway header (older era); left intact.
    deriving (Eq, Show)


-- | Load a cardano-cli KES signing key TextEnvelope (@kes.skey@) into the
-- unsound-pure crypto type. The @cborHex@ field is a CBOR byte-string wrapping
-- the raw KES sign-key bytes, which is exactly what 'KES.decodeUnsoundPureSignKeyKES'
-- consumes.
loadKesSignKey :: FilePath -> IO (KES.UnsoundPureSignKeyKES (KES StandardCrypto))
loadKesSignKey path = do
    parsed <- eitherDecodeFileStrict path
    envelope <- either (fail . ("kes.skey parse: " <>)) pure parsed
    cborHexText <-
        either (fail . ("kes.skey cborHex: " <>)) pure $
            parseEither (withObject "TextEnvelope" (.: "cborHex")) envelope
    cborBytes <-
        either (fail . ("kes.skey hex: " <>)) pure $
            Base16.decode (encodeUtf8 (cborHexText :: Text))
    case deserialiseFromBytes KES.decodeUnsoundPureSignKeyKES (LBS.fromStrict cborBytes) of
        Right (rest, key)
            | LBS.null rest -> pure key
            | otherwise -> fail "kes.skey: trailing bytes after sign key"
        Left err -> fail ("kes.skey decode: " <> show err)


-- | The raw-serialised hot KES verification key derived from our sign key.
-- Used to decide whether a header was produced by /our/ pool.
kesHotVerKeyBytes :: KES.UnsoundPureSignKeyKES (KES StandardCrypto) -> BS.ByteString
kesHotVerKeyBytes = KES.rawSerialiseVerKeyKES . KES.unsoundPureDeriveVerKeyKES


-- | Re-sign a single header if (and only if) it is a current-era (Conway)
-- Praos header whose operational certificate hot key matches our KES key.
-- Returns the (possibly rebuilt) header and a description of what happened.
reSignHeader :: ReSignParams -> Header -> (Header, ReSignOutcome)
reSignHeader params hdr = case hdr of
    HeaderConway shelleyHdr ->
        let praosHdr = shelleyHeaderRaw shelleyHdr
            body = Praos.headerBody praosHdr
            ocert = Praos.hbOCert body
            SlotNo slot = Praos.hbSlotNo body
            period = slot `div` fromIntegral (rspSlotsPerKESPeriod params)
            ourHot = kesHotVerKeyBytes (rspKesSignKey params)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
        in  if ourHot == theirHot
                then
                    let sig' =
                            KES.SignedKES $
                                KES.unsoundPureSignKES
                                    ()
                                    (fromIntegral period)
                                    body
                                    (rspKesSignKey params)
                        praosHdr' = Praos.Header body sig'
                        rebuilt = HeaderConway (mkShelleyHeader praosHdr')
                    in  (rebuilt, ReSignedConway (fromIntegral period))
                else (hdr, PassthroughForeignPool)
    _ -> (hdr, PassthroughNonConway)


-- | The header transform used on the codec encode path.
reSignOrPassthrough :: ReSignParams -> Header -> Header
reSignOrPassthrough params = fst . reSignHeader params


-- | A ChainSync codec that re-signs each Conway header from our pool on the
-- encode (serve) path. Decoding is unchanged.
resignCodec
    :: ReSignParams
    -> Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
resignCodec params =
    ChainSync.codecChainSync enc decHeader encPoint decPoint encTip decTip
  where
    enc = encHeader . reSignOrPassthrough params


-- | Offer exactly one target header, and only after the client proves it has
-- the exact real parent. (Copied from the KES adversary; the served header is
-- re-signed on the codec encode path via 'resignCodec'.)
singleTargetServer
    :: (String -> IO ())
    -> Point
    -> Header
    -> Tip
    -> ChainSyncServer Header Point Tip IO ()
singleTargetServer log_ parentPoint target tip = ChainSyncServer (pure (idle False))
  where
    idle served =
        ServerStIdle
            { recvMsgRequestNext =
                if served
                    then pure (Right (foreverAwait served))
                    else do
                        log_ "opcert-target: serving one re-signed valid-control header"
                        pure
                            ( Left
                                ( SendMsgRollForward
                                    target
                                    tip
                                    (ChainSyncServer (pure (idle True)))
                                )
                            )
            , recvMsgFindIntersect = \points ->
                case find (== parentPoint) points of
                    Just point -> do
                        log_ "opcert-target: exact parent intersection found"
                        pure
                            ( SendMsgIntersectFound
                                point
                                tip
                                (ChainSyncServer (pure (idle False)))
                            )
                    Nothing -> do
                        log_ "opcert-target: exact parent absent; refusing intersection"
                        pure
                            ( SendMsgIntersectNotFound
                                tip
                                (ChainSyncServer (pure (idle False)))
                            )
            , recvMsgDoneClient = log_ "opcert-target: client done"
            }

    foreverAwait served = do
        threadDelay 1_000_000
        foreverAwait served
