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
-- The @valid-control@ case re-signs the decoded header's OWN 'HeaderBody'
-- UNCHANGED at the header's current KES /evolution/. A correct re-sign
-- reproduces a byte-identical, valid header, so an isolated node ADOPTS it.
--
-- The six rule mutations each start from a REAL captured pool1 header and
-- change ONLY the operational-certificate field (then re-sign), so the VRF
-- and the parent linkage stay valid and the ONLY rejection cause is the
-- opcert rule under test. cardano-node validates the opcert (KES/OCERT) rules
-- BEFORE the VRF, so a header that reuses a real body's VRF is rejected on the
-- opcert rule first.
module DwarfOpcertAdversary
    ( ReSignParams (..)
    , ReSignOutcome (..)
    , Mutation (..)
    , KeySet (..)
    , CaseResult (..)
    , loadKesSignKey
    , loadColdSignKey
    , kesHotVerKeyBytes
    , kesEvolutions
    , reSignHeader
    , reSignOrPassthrough
    , resignCodec
    , plainCodec
    , applyCase
    , caseEligible
    , caseMutationName
    , singleTargetServer
    , CaseSpec (..)
    , parseCaseSpec
    , caseSpecByteSeed
    , applyCaseSpec
    , applyKesEvolution
    , applyErrorPrecedence
    , applyCounterEdge
    , RuleParams (..)
    , defaultRuleParams
    , ruleParamsFromSpec
    , applyCaseWith
    , seedBytes
    , wrongColdKeyRaw
    , wrongKesKeyRaw
    , reEncodeOpcert
    , oPCERT_FIELD_MUTATIONS
    , isOpcertNode
    , mutateFirstOpcert
    ) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.Serialise (DeserialiseFailure)
import Control.Concurrent (threadDelay)
import Data.Aeson (eitherDecode, eitherDecodeFileStrict, (.:), (.:?), (.!=))
import Data.Aeson qualified as A
import Data.Aeson.Key qualified as AKey
import Data.Aeson.KeyMap qualified as AKM
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Data.Bits (shiftL, shiftR, (.&.), (.|.))
import Data.Maybe (fromMaybe)
import Data.Word (Word8)
import System.Random (mkStdGen, randomR, randomRs)
import Data.Aeson.Types (parseEither, withObject)
import Data.Aeson.Types qualified as AesonTypes
import Data.ByteString qualified as BS
import Data.ByteString.Base16 qualified as Base16
import Data.ByteString.Lazy qualified as LBS
import Data.List (find)
import Data.Text (Text)
import Data.Text.Encoding (encodeUtf8)
import Data.Word (Word64)

import Cardano.Crypto.DSIGN (Ed25519DSIGN, SignKeyDSIGN, decodeSignKeyDSIGN, deriveVerKeyDSIGN, genKeyDSIGN, rawSerialiseVerKeyDSIGN)
import Cardano.Crypto.KES qualified as KES
import Cardano.Crypto.Seed (mkSeedFromBytes)
import Cardano.Ledger.Keys (signedDSIGN)
import Cardano.Protocol.Crypto (KES, StandardCrypto)
import Cardano.Protocol.Praos.BlockHeader qualified as Praos
import Cardano.Protocol.TPraos.OCert
    ( KESPeriod (..)
    , OCert (..)
    , OCertSignable (..)
    )
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
    -- unsound-pure primitive evolves it internally to the target evolution.
    , rspSlotsPerKESPeriod :: !Word
    -- ^ @slotsPerKESPeriod@ from shelley-genesis; maps slot -> KES period.
    }


-- | The result of applying a re-sign to a single header.
data ReSignOutcome
    = ReSignedConway !Word
    -- ^ A Conway header whose OCert hot key matches ours was re-signed at the
    -- given KES evolution.
    | PassthroughForeignPool
    -- ^ A Conway header from a different pool (hot key mismatch); left intact.
    | PassthroughNonConway
    -- ^ Not a Conway header (older era); left intact.
    deriving (Eq, Show)


-- | The six operational-certificate rule mutations, named 1:1 with the
-- ouroboros-consensus reference generator
-- (@Test.Ouroboros.Consensus.Protocol.Praos.Header.Mutation@).
data Mutation
    = NoMutation
    | MutateColdKey
    | MutateCounterUnder
    | MutateCounterOver1
    | MutateKESPeriod
    | MutateKESPeriodBefore
    | MutateKESKey
    | MutateErrorPrecedence
    | MutateCounterEdge
    deriving (Eq, Show)


-- | All the pool-1 keys + protocol params the mutations need. The cold key is
-- the pool's REAL cold key (so a counter/KES-period mutation can re-sign a
-- structurally-valid opcert whose ONLY defect is the rule under test); the
-- 'MutateColdKey' / 'MutateKESKey' cases substitute a fresh (wrong) key.
data KeySet = KeySet
    { ksKesSignKey :: !(KES.UnsoundPureSignKeyKES (KES StandardCrypto))
    , ksColdSignKey :: !(SignKeyDSIGN Ed25519DSIGN)
    , ksSlotsPerKESPeriod :: !Word
    , ksMaxKESEvo :: !Word
    , ksForeignColdSignKey :: !(Maybe (SignKeyDSIGN Ed25519DSIGN))
    -- ^ A DIFFERENT real devnet pool's cold key, loaded only for the
    -- cross-pool-confusion family. Signs the @cross-pool@ case's opcert so the
    -- certificate is authorized by the wrong pool; 'Nothing' for every other
    -- case (the field is unused there).
    }


-- | The outcome of preparing a case header from a captured pool1 header.
data CaseResult = CaseResult
    { crHeader :: !Header
    , crMutation :: !Mutation
    , crExpectedVerdict :: !String -- ^ "accept" | "reject"
    }


-- | Load a cardano-cli KES signing key TextEnvelope (@kes.skey@) into the
-- unsound-pure crypto type. The @cborHex@ field is a CBOR byte-string wrapping
-- the raw KES sign-key bytes, which is exactly what 'KES.decodeUnsoundPureSignKeyKES'
-- consumes.
loadKesSignKey :: FilePath -> IO (KES.UnsoundPureSignKeyKES (KES StandardCrypto))
loadKesSignKey path = do
    cborBytes <- loadEnvelopeCbor path
    case deserialiseFromBytes KES.decodeUnsoundPureSignKeyKES (LBS.fromStrict cborBytes) of
        Right (rest, key)
            | LBS.null rest -> pure key
            | otherwise -> fail "kes.skey: trailing bytes after sign key"
        Left err -> fail ("kes.skey decode: " <> show err)


-- | Load a cardano-cli cold signing key TextEnvelope (@cold.skey@,
-- @StakePoolSigningKey_ed25519@) into the Ed25519 DSIGN sign key. The
-- @cborHex@ is a CBOR byte-string wrapping the 32-byte ed25519 seed, which is
-- what 'decodeSignKeyDSIGN' consumes.
loadColdSignKey :: FilePath -> IO (SignKeyDSIGN Ed25519DSIGN)
loadColdSignKey path = do
    cborBytes <- loadEnvelopeCbor path
    case deserialiseFromBytes decodeSignKeyDSIGN (LBS.fromStrict cborBytes) of
        Right (rest, key)
            | LBS.null rest -> pure key
            | otherwise -> fail "cold.skey: trailing bytes after sign key"
        Left err -> fail ("cold.skey decode: " <> show err)


-- | Extract and hex-decode the @cborHex@ of a cardano-cli TextEnvelope.
loadEnvelopeCbor :: FilePath -> IO BS.ByteString
loadEnvelopeCbor path = do
    parsed <- eitherDecodeFileStrict path
    envelope <- either (fail . ("envelope parse: " <>)) pure parsed
    cborHexText <-
        either (fail . ("envelope cborHex: " <>)) pure $
            parseEither (withObject "TextEnvelope" (.: "cborHex")) envelope
    either (fail . ("envelope hex: " <>)) pure $
        Base16.decode (encodeUtf8 (cborHexText :: Text))


-- | The raw-serialised hot KES verification key derived from our sign key.
-- Used to decide whether a header was produced by /our/ pool.
kesHotVerKeyBytes :: KES.UnsoundPureSignKeyKES (KES StandardCrypto) -> BS.ByteString
kesHotVerKeyBytes = KES.rawSerialiseVerKeyKES . KES.unsoundPureDeriveVerKeyKES


-- | The KES /evolution/ to sign at: the number of periods elapsed since the
-- opcert start period, i.e. @currentKESPeriod - ocertKESPeriod@ (clamped at 0).
--
-- Signing at the ABSOLUTE period @slot `div` slotsPerKESPeriod@ is wrong the
-- moment the chain crosses a KES-period rollover (slot >= slotsPerKESPeriod):
-- the unsound-pure primitive evolves the on-disk period-0 key by the count
-- passed to it, and the node verifies at @t = kp - c0@ (Praos
-- 'doValidateKESSignature'). Passing the evolution count relative to the
-- opcert start period matches the node and keeps valid-control adopted across
-- the rollover. On a fresh devnet @ocertKESPeriod == 0@, so this equals the
-- absolute period; it only diverges once opcerts are issued at a later period.
kesEvolutions :: Word -> SlotNo -> KESPeriod -> Word
kesEvolutions slots (SlotNo slot) (KESPeriod c0) =
    let cur = fromIntegral slot `div` slots
    in  if cur >= c0 then cur - c0 else 0


-- | Evolve the on-disk (period-0) KES sign key forward to @target@ periods.
-- 'unsoundPureSignKES' signs at the key's CURRENT period; it does NOT evolve
-- the key itself. So to produce a signature the node will accept at evolution
-- @t = currentKESPeriod - ocertKESPeriod@ (which is > 0 once the chain crosses
-- a KES-period rollover), the key must first be stepped forward @t@ times.
evolveKesTo :: Word -> KES.UnsoundPureSignKeyKES (KES StandardCrypto) -> KES.UnsoundPureSignKeyKES (KES StandardCrypto)
evolveKesTo target = go 0
  where
    go p k
        | p >= target = k
        | otherwise = case KES.unsoundPureUpdateKES () k p of
            Just k' -> go (p + 1) k'
            Nothing -> error ("evolveKesTo: KES key exhausted at period " <> show p)


-- | Sign @msg@ at KES evolution @evol@ with @key@ (evolving it first).
signAtEvolution
    :: KES.Signable (KES StandardCrypto) a
    => KES.UnsoundPureSignKeyKES (KES StandardCrypto)
    -> Word
    -> a
    -> KES.SignedKES (KES StandardCrypto) a
signAtEvolution key evol msg =
    KES.SignedKES (KES.unsoundPureSignKES () evol msg (evolveKesTo evol key))


-- | Like 'evolveKesTo' but 'Nothing' instead of crashing when the key is
-- exhausted before @target@ (used by the kes-evolution family, which must
-- fail-close an unreachable target rather than error).
evolveKesToMaybe :: Word -> KES.UnsoundPureSignKeyKES (KES StandardCrypto) -> Maybe (KES.UnsoundPureSignKeyKES (KES StandardCrypto))
evolveKesToMaybe target = go 0
  where
    go p k
        | p >= target = Just k
        | otherwise = case KES.unsoundPureUpdateKES () k p of
            Just k' -> go (p + 1) k'
            Nothing -> Nothing


-- | Like 'signAtEvolution' but 'Nothing' when the key cannot evolve to @evol@.
signAtEvolutionMaybe
    :: KES.Signable (KES StandardCrypto) a
    => KES.UnsoundPureSignKeyKES (KES StandardCrypto)
    -> Word
    -> a
    -> Maybe (KES.SignedKES (KES StandardCrypto) a)
signAtEvolutionMaybe key evol msg =
    fmap (\k -> KES.SignedKES (KES.unsoundPureSignKES () evol msg k)) (evolveKesToMaybe evol key)


-- | Re-sign a single header if (and only if) it is a current-era (Conway)
-- Praos header whose operational certificate hot key matches our KES key.
-- Returns the (possibly rebuilt) header and a description of what happened.
reSignHeader :: ReSignParams -> Header -> (Header, ReSignOutcome)
reSignHeader params hdr = case hdr of
    HeaderConway shelleyHdr ->
        let praosHdr = shelleyHeaderRaw shelleyHdr
            body = Praos.headerBody praosHdr
            ocert = Praos.hbOCert body
            evol = kesEvolutions (rspSlotsPerKESPeriod params) (Praos.hbSlotNo body) (ocertKESPeriod ocert)
            ourHot = kesHotVerKeyBytes (rspKesSignKey params)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
        in  if ourHot == theirHot
                then
                    let sig' = signAtEvolution (rspKesSignKey params) evol body
                        praosHdr' = Praos.Header body sig'
                        rebuilt = HeaderConway (mkShelleyHeader praosHdr')
                    in  (rebuilt, ReSignedConway evol)
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


-- | A plain (non-mutating) ChainSync codec: headers are served exactly as held.
-- Used by the case runner, which does all header transformation server-side so
-- the one injected mutation is not clobbered by a codec re-sign.
plainCodec
    :: Codec (ChainSync Header Point Tip) DeserialiseFailure IO LBS.ByteString
plainCodec =
    ChainSync.codecChainSync encHeader decHeader encPoint decPoint encTip decTip


-- | Is this header a current-era (Conway) header from OUR pool (the one whose
-- opcert rules the cases probe)? Only such a header is eligible to carry a case
-- mutation; the runner serves every other header unchanged and waits.
caseEligible :: KeySet -> Header -> Bool
caseEligible ks hdr = case hdr of
    HeaderConway shelleyHdr ->
        let ocert = Praos.hbOCert (Praos.headerBody (shelleyHeaderRaw shelleyHdr))
            ourHot = kesHotVerKeyBytes (ksKesSignKey ks)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
        in  ourHot == theirHot
    _ -> False


-- | Human name of a mutation, matching the reference generator + case table.
caseMutationName :: Mutation -> String
caseMutationName = \case
    NoMutation -> "NoMutation"
    MutateColdKey -> "MutateColdKey"
    MutateCounterUnder -> "MutateCounterUnder"
    MutateCounterOver1 -> "MutateCounterOver1"
    MutateKESPeriod -> "MutateKESPeriod"
    MutateKESPeriodBefore -> "MutateKESPeriodBefore"
    MutateKESKey -> "MutateKESKey"
    MutateErrorPrecedence -> "MutateErrorPrecedence"
    MutateCounterEdge -> "MutateCounterEdge"


-- | Prepare the case header from a REAL captured pool1 Conway header. Changes
-- ONLY the operational-certificate field (or, for valid-control, nothing) and
-- re-signs, so parent linkage + VRF stay valid. Returns 'Left' with a reason
-- when the case's opcert rule cannot be reached from this header on this chain
-- (a finding, not a silent pass).
--
-- @recordedCounter@ is the ocert counter the node currently has recorded for
-- pool1 (the counter of the honest chain it is synced on = the captured
-- header's own @ocertN@); it decides whether the counter mutations can reach
-- their rule.
applyCase :: KeySet -> String -> Header -> Either String CaseResult
applyCase ks caseId hdr = applyCaseWith ks defaultRuleParams caseId hdr


-- | Per-iteration boundary magnitudes for the rules-differential sweep. The
-- forger consumes these so the served header's rule violation varies its
-- MAGNITUDE across iterations (not just which rule), hunting magnitude-dependent
-- cardano-vs-amaru divergences. 'Nothing' everywhere reproduces the historic
-- fixed behaviour (counter jump +2, kes period +1 ahead, fixed wrong keys), so
-- the fixed (non-soak) scenarios are byte-identical.
data RuleParams = RuleParams
    { rpCounterJump     :: !(Maybe Integer)
    , rpKesPeriodsAhead :: !(Maybe Integer)
    , rpWrongKeySeed    :: !(Maybe Int)
    }
    deriving (Eq, Show)

defaultRuleParams :: RuleParams
defaultRuleParams = RuleParams Nothing Nothing Nothing

-- | Build the sweep params from a case spec. The wrong-key seed reuses the
-- per-iteration @byte_seed@ the generator already records, so the cold-/hot-key
-- rules vary their unauthorized key without a new spec field.
ruleParamsFromSpec :: CaseSpec -> RuleParams
ruleParamsFromSpec spec =
    RuleParams (csCounterJump spec) (csKesPeriodsAhead spec) (csByteSeed spec)

-- | 32 deterministic bytes from a seed and a domain tag (distinct tags give
-- independent streams so the cold-key and KES-key wrong keys never coincide).
seedBytes :: Int -> Word8 -> BS.ByteString
seedBytes s tag = BS.pack (take 32 (randomRs (0, 255) (mkStdGen (s * 131 + fromIntegral tag))))

-- | The WRONG cold key for the cold-key-unauthorized rule: per-iteration
-- (seed-derived) when a seed is given, else the historic fixed key.
wrongColdKeyFor :: Maybe Int -> SignKeyDSIGN Ed25519DSIGN
wrongColdKeyFor ms =
    genKeyDSIGN (mkSeedFromBytes (maybe (BS.replicate 32 0x11) (\s -> seedBytes s 0xC0) ms))

-- | The WRONG KES key for the hot-key-mismatch rule: per-iteration when a seed
-- is given, else the historic fixed key.
wrongKesKeyFor :: Maybe Int -> KES.UnsoundPureSignKeyKES (KES StandardCrypto)
wrongKesKeyFor ms =
    KES.unsoundPureGenKeyKES (mkSeedFromBytes (maybe (BS.replicate 32 0x22) (\s -> seedBytes s 0x8E) ms))

-- | Raw-serialised verification key of the wrong cold key (for tests: distinct
-- seeds must give distinct keys, hence distinct served bytes).
wrongColdKeyRaw :: Maybe Int -> BS.ByteString
wrongColdKeyRaw = rawSerialiseVerKeyDSIGN . deriveVerKeyDSIGN . wrongColdKeyFor

-- | Raw-serialised hot verification key of the wrong KES key (for tests).
wrongKesKeyRaw :: Maybe Int -> BS.ByteString
wrongKesKeyRaw = kesHotVerKeyBytes . wrongKesKeyFor

applyCaseWith :: KeySet -> RuleParams -> String -> Header -> Either String CaseResult
applyCaseWith ks rp caseId hdr = case hdr of
    HeaderConway shelleyHdr ->
        let praosHdr = shelleyHeaderRaw shelleyHdr
            body = Praos.headerBody praosHdr
            ocert = Praos.hbOCert body
            SlotNo slot = Praos.hbSlotNo body
            currentKES = fromIntegral slot `div` ksSlotsPerKESPeriod ks :: Word
            KESPeriod c0 = ocertKESPeriod ocert
            realN = ocertN ocert
            ourHot = kesHotVerKeyBytes (ksKesSignKey ks)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
        in  if ourHot /= theirHot
                then Left "captured header is not from our pool (hot-key mismatch)"
                else prepare body ocert slot currentKES c0 realN caseId
    _ -> Left "captured header is not a Conway header"
  where
    prepare body ocert slot currentKES c0 realN cid = case cid of
        "valid-control" ->
            -- Serve the real header UNCHANGED (byte-identical valid-control).
            Right (CaseResult hdr NoMutation "accept")
        "counter-plus-one" ->
            -- ocertN incremented by exactly 1: n == recorded + 1, accepted.
            Right $ finishOCert body (ocert{ ocertN = realN + 1
                                           , ocertSigma = coldSig (ocertVkHot ocert) (realN + 1) (ocertKESPeriod ocert) })
                                     NoMutation "accept"
        "cold-key-unauthorized" ->
            -- Re-sign the opcert with a DIFFERENT cold key; leave hbVk (the
            -- header cold vkey the node verifies against) original -> the
            -- ocert signature fails: InvalidSignatureOCERT.
            Right $ finishOCert body (ocert{ ocertSigma = wrongColdSig (ocertVkHot ocert) realN (ocertKESPeriod ocert) })
                                     MutateColdKey "reject"
        "cross-pool" ->
            -- Cross-pool confusion: sign the opcert with a DIFFERENT REAL pool's
            -- cold key (loaded into ksForeignColdSignKey), leaving hbVk (the
            -- header issuer cold vkey the node verifies the opcert signature
            -- against) as our pool's. A node that scopes opcert authorization
            -- per pool rejects it (the signature is by another pool's cold key):
            -- InvalidSignatureOCERT. An ACCEPT would mean the node accepted
            -- another pool's authorization -- a cross-pool-confusion finding.
            case ksForeignColdSignKey ks of
                Nothing -> Left "cross-pool unreachable: no foreign cold key loaded (--foreign-cold-skey / foreign_pool param missing)"
                Just fcold ->
                    Right $ finishOCert body (ocert{ ocertSigma = foreignColdSig fcold (ocertVkHot ocert) realN (ocertKESPeriod ocert) })
                                             MutateColdKey "reject"
        "counter-behind"
            | realN < 1 ->
                Left $ "counter-behind unreachable: recorded pool counter is "
                    <> show realN <> " (0); CounterTooSmallOCERT needs recorded >= 1, "
                    <> "i.e. a prior opcert rotation on this devnet"
            | otherwise ->
                Right $ finishOCert body (ocert{ ocertN = realN - 1
                                               , ocertSigma = coldSig (ocertVkHot ocert) (realN - 1) (ocertKESPeriod ocert) })
                                         MutateCounterUnder "reject"
        "counter-jump" ->
            -- ocertN more than +1 over recorded -> CounterOverIncrementedOCERT.
            -- The jump magnitude is per-iteration (rules-differential sweep):
            -- rpCounterJump, default +2, clamped >= 2 (never the accepted +1).
            let jumpedN = realN + counterJumpN
            in  Right $ finishOCert body (ocert{ ocertN = jumpedN
                                               , ocertSigma = coldSig (ocertVkHot ocert) jumpedN (ocertKESPeriod ocert) })
                                         MutateCounterOver1 "reject"
        "kes-before-window" ->
            -- ocert start KES period AHEAD of the header's slot period (c0 > kp)
            -- -> KESBeforeStartOCERT. The periods-ahead magnitude is
            -- per-iteration (sweep): rpKesPeriodsAhead, default +1, clamped >= 1.
            let newC0 = KESPeriod (currentKES + kesAheadW)
            in  Right $ finishOCert body (ocert{ ocertKESPeriod = newC0
                                               , ocertSigma = coldSig (ocertVkHot ocert) realN newC0 })
                                         MutateKESPeriod "reject"
        "kes-after-window"
            | currentKES < ksMaxKESEvo ks + 1 ->
                Left $ "kes-after-window unreachable: current KES period is "
                    <> show currentKES <> " < maxKESEvolutions+1 (" <> show (ksMaxKESEvo ks + 1)
                    <> "); KESAfterEndOCERT needs the chain aged >= maxKESEvolutions KES periods"
            | otherwise ->
                let newC0 = KESPeriod (currentKES - (ksMaxKESEvo ks + 1))
                in  Right $ finishOCert body (ocert{ ocertKESPeriod = newC0
                                                   , ocertSigma = coldSig (ocertVkHot ocert) realN newC0 })
                                             MutateKESPeriodBefore "reject"
        "hot-key-mismatch" ->
            -- Sign the body with a DIFFERENT KES key; leave the opcert (and its
            -- ocertVkHot) intact -> InvalidKesSignatureOCERT.
            let sig' = signAtEvolution wrongKesKey (kesEvol slot c0) body
            in  Right (CaseResult (HeaderConway (mkShelleyHeader (Praos.Header body sig'))) MutateKESKey "reject")
        other -> Left ("unknown case id: " <> other)
      where
        -- Re-sign the (mutated-opcert) body with OUR real KES key at the
        -- evolution the node will compute from the (possibly mutated) opcert
        -- start period, then wrap back into a Conway header.
        finishOCert :: Praos.HeaderBody StandardCrypto -> OCert StandardCrypto -> Mutation -> String -> CaseResult
        finishOCert body0 newOcert mut verdict =
            let newBody = body0{ Praos.hbOCert = newOcert }
                KESPeriod c0' = ocertKESPeriod newOcert
                sig' = signAtEvolution (ksKesSignKey ks) (kesEvol slot c0') newBody
            in  CaseResult (HeaderConway (mkShelleyHeader (Praos.Header newBody sig'))) mut verdict

        kesEvol s c0' = let cur = fromIntegral s `div` ksSlotsPerKESPeriod ks :: Word
                        in if cur >= c0' then cur - c0' else 0

        -- Per-iteration sweep magnitudes (default to the historic fixed values).
        counterJumpN = fromIntegral (max 2 (fromMaybe 2 (rpCounterJump rp))) :: Word64
        kesAheadW    = fromIntegral (max 1 (fromMaybe 1 (rpKesPeriodsAhead rp))) :: Word
        -- A valid opcert signature by the REAL pool cold key.
        coldSig vkHot n p = signedDSIGN (ksColdSignKey ks) (OCertSignable vkHot n p)
        -- An opcert signature by a fresh WRONG cold key; the wrong-key seed is
        -- per-iteration (rpWrongKeySeed, from byte_seed) so the sweep varies the
        -- unauthorized key, falling back to the historic fixed key when absent.
        wrongColdSig vkHot n p =
            signedDSIGN (wrongColdKeyFor (rpWrongKeySeed rp)) (OCertSignable vkHot n p)
        -- An opcert signature by a DIFFERENT REAL pool's cold key (cross-pool).
        foreignColdSig fcold vkHot n p = signedDSIGN fcold (OCertSignable vkHot n p)
        wrongKesKey = wrongKesKeyFor (rpWrongKeySeed rp)


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


-- ===================================================================
-- Soak mode: seed-derived case-spec override + encoding-form re-encoder
-- ===================================================================

-- | A seed-derived spec parsed from a @--case-spec@ JSON file. When present it
-- deterministically overrides the hard-coded per-caseId behaviour of
-- 'applyCase'; absent, the forger keeps its current deterministic behaviour so
-- the fixed (non-soak) scenarios are unchanged.
data CaseSpec = CaseSpec
    { csBaseCase       :: !String
    , csSeed           :: !Int
    , csByteSeed       :: !(Maybe Int)
    , csEncodingForm   :: !(Maybe String)
    , csTrailingLen    :: !(Maybe Int)
    , csCounterDelta   :: !(Maybe Integer)
    , csKesPeriodFrac  :: !(Maybe Double)
    , csReplayCounter  :: !(Maybe Word)
    , csSlotOffsetFrac :: !(Maybe Double)
    , csForeignPool    :: !(Maybe String)
    , csKesEvoDelta    :: !(Maybe Integer)
    , csCounterJump    :: !(Maybe Integer)
    , csKesPeriodsAhead :: !(Maybe Integer)
    , csRules          :: !(Maybe [String])
    , csCounterValue   :: !(Maybe Integer)
    }
    deriving (Eq, Show)

-- | The structural CBOR deviations the encoding-form family may request. Raw
-- byte fuzzing is deliberately NOT here (owned by the CBOR fuzzers), so an
-- unknown form fails closed in 'parseCaseSpec'.
encodingForms :: [String]
encodingForms =
    [ "noncanonical-int", "definite-array", "indefinite-array"
    , "extra-map-key", "duplicate-map-key", "missing-optional-key", "trailing-bytes" ]

-- | Parse a @--case-spec@ JSON document @{base_case, seed, params}@.
parseCaseSpec :: LBS.ByteString -> Either String CaseSpec
parseCaseSpec raw = do
    value <- eitherDecode raw
    spec <- parseEither specParser value
    case csEncodingForm spec of
        Just form | form `notElem` encodingForms -> Left ("unknown encoding form: " <> form)
        _ -> Right spec
  where
    specParser = withObject "CaseSpec" $ \o -> do
        base    <- o .: "base_case"
        seed    <- o .: "seed"
        mparams <- o .:? "params"
        let paramsObj = case mparams of
                Just (A.Object km) -> km
                _ -> AKM.empty
            getP :: A.FromJSON a => String -> AesonTypes.Parser (Maybe a)
            getP k = case AKM.lookup (AKey.fromString k) paramsObj of
                Nothing -> pure Nothing
                Just A.Null -> pure Nothing
                Just v -> Just <$> A.parseJSON v
        bseed  <- getP "byte_seed"
        form   <- getP "encoding_form"
        tlen   <- getP "trailing_len"
        cdelta <- getP "counter_delta"
        kfrac  <- getP "kes_period_fraction"
        replay <- getP "replay_counter"
        soff   <- getP "slot_offset_fraction"
        fpool  <- getP "foreign_pool"
        kevo   <- getP "kes_evolution_delta"
        cjump  <- getP "counter_jump"
        kahead <- getP "kes_periods_ahead"
        rules  <- getP "rules"
        cval   <- getP "counter_value"
        pure (CaseSpec base seed bseed form tlen cdelta kfrac replay soff fpool kevo cjump kahead rules cval)

-- | The seed that drives the encoding-form byte re-encoding for THIS case. The
-- generator derives a distinct per-iteration @byte_seed@ (in @params@) from
-- @seed+iteration@, so each iteration of a form varies its re-encoding while
-- staying replayable. Falls back to the campaign @seed@ only when a spec omits
-- @byte_seed@ (older single-spec scenarios), never to a hard-coded constant.
caseSpecByteSeed :: CaseSpec -> Int
caseSpecByteSeed spec = fromMaybe (csSeed spec) (csByteSeed spec)


-- | Deterministic override of 'applyCase'. When the spec is 'Nothing' the
-- forger keeps its current per-caseId behaviour. When a spec is present the
-- base_case selects the typed opcert mutation (the encoding-form byte deviation
-- is applied separately, on the served header bytes, by 'reEncodeOpcert'); the
-- numeric fields are echoed into evidence so a finding row is replayable.
applyCaseSpec :: KeySet -> Maybe CaseSpec -> String -> Header -> Either String CaseResult
applyCaseSpec ks Nothing    caseId hdr = applyCase ks caseId hdr
applyCaseSpec ks (Just spec) _     hdr = case csBaseCase spec of
    "kes-evolution"    -> applyKesEvolution ks spec hdr
    "error-precedence" -> applyErrorPrecedence ks spec hdr
    "counter-edge"     -> applyCounterEdge ks spec hdr
    other              -> applyCaseWith ks (ruleParamsFromSpec spec) other hdr


-- | Family #5 counter-edge-cases: serve a header whose opcert counter is at an
-- EDGE value relative to the pool\'s recorded counter, and leave every other
-- field valid. The target counter is either @realN + counter_jump@ (extreme
-- forward jumps, e.g. 2^16..2^63 -> counter-too-large) or an ABSOLUTE
-- @counter_value@ (overflow boundary max-uint64 -> too-large; 0 -> too-small
-- when the pool has rotated so recorded >= 1). The opcert signature and KES
-- signature are valid, so the ONLY defect is the counter, and both nodes reject
-- (too-large on a fresh devnet; too-small only after a rotation).
--
-- Fail-closed reachability guard: a target equal to @realN@ (reuse) or
-- @realN + 1@ (a valid rotation) would be ACCEPTED, so it returns Left
-- (inconclusive) rather than a valid header. This is exactly what makes the
-- too-small (counter = 0) case fail-closed on a FRESH devnet, where recorded is
-- 0: target 0 == realN 0 -> Left. On a rotated pool (recorded >= 1) the same 0
-- is below recorded -> a genuine too-small rejection.
applyCounterEdge :: KeySet -> CaseSpec -> Header -> Either String CaseResult
applyCounterEdge ks spec hdr = case hdr of
    HeaderConway shelleyHdr ->
        let praosHdr = shelleyHeaderRaw shelleyHdr
            body = Praos.headerBody praosHdr
            ocert = Praos.hbOCert body
            realN = ocertN ocert
            ourHot = kesHotVerKeyBytes (ksKesSignKey ks)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
            target = case csCounterValue spec of
                Just v  -> fromInteger v :: Word64
                Nothing -> realN + fromIntegral (fromMaybe 0 (csCounterJump spec))
        in  if ourHot /= theirHot
                then Left "counter-edge: captured header is not from our pool (hot-key mismatch)"
            else if target == realN || target == realN + 1
                then Left ("counter-edge unreachable: target counter " <> show target
                           <> " is a valid reuse/rotation of recorded " <> show realN
                           <> " (would be accepted; a too-small case needs a prior rotation so recorded >= 1)")
            else
                let evol = kesEvolutions (ksSlotsPerKESPeriod ks) (Praos.hbSlotNo body) (ocertKESPeriod ocert)
                    sigma = signedDSIGN (ksColdSignKey ks) (OCertSignable (ocertVkHot ocert) target (ocertKESPeriod ocert))
                    newOcert = ocert { ocertN = target, ocertSigma = sigma }
                    newBody = body { Praos.hbOCert = newOcert }
                    kesSig = signAtEvolution (ksKesSignKey ks) evol newBody
                in  Right (CaseResult (HeaderConway (mkShelleyHeader (Praos.Header newBody kesSig)))
                                      MutateCounterEdge "reject")
    _ -> Left "counter-edge: captured header is not a Conway header"


-- | Break TWO opcert rules in a SINGLE header (family #4 error-precedence). The
-- combo is @csRules@ (exactly two of cold-key-unauthorized, counter-jump,
-- kes-before-window, hot-key-mismatch -- all reachable on a fresh mixed devnet).
-- Each rule\'s mutation is stacked into one header, then the header is signed, so
-- BOTH violations are present and each node reports whichever rule its validator
-- checks FIRST. The driver compares the two nodes\' canonical reported rules
-- (reason parity): same rule => precedence agrees; different rule => a
-- precedence-divergence finding (a both-reject reason_mismatch). Magnitudes
-- (counter_jump / kes_periods_ahead) and the wrong-key seed (byte_seed) are the
-- same per-iteration params the single-rule sweep uses. Fail-closed: a combo
-- that is not exactly two recognised rules returns Left (inconclusive), never a
-- valid header that could be falsely accepted.
applyErrorPrecedence :: KeySet -> CaseSpec -> Header -> Either String CaseResult
applyErrorPrecedence ks spec hdr = case hdr of
    HeaderConway shelleyHdr ->
        let praosHdr = shelleyHeaderRaw shelleyHdr
            body = Praos.headerBody praosHdr
            ocert = Praos.hbOCert body
            SlotNo slot = Praos.hbSlotNo body
            currentKES = fromIntegral slot `div` ksSlotsPerKESPeriod ks :: Word
            realN = ocertN ocert
            ourHot = kesHotVerKeyBytes (ksKesSignKey ks)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
            rules = fromMaybe [] (csRules spec)
            hasCounterJump = "counter-jump" `elem` rules
            hasKesBefore   = "kes-before-window" `elem` rules
            hasColdKey     = "cold-key-unauthorized" `elem` rules
            hasHotKey      = "hot-key-mismatch" `elem` rules
            recognised = length (filter id [hasCounterJump, hasKesBefore, hasColdKey, hasHotKey])
        in  if ourHot /= theirHot
                then Left "error-precedence: captured header is not from our pool (hot-key mismatch)"
            else if recognised /= 2
                then Left ("error-precedence unreachable: need exactly two of "
                           <> "{cold-key-unauthorized,counter-jump,kes-before-window,hot-key-mismatch}, got "
                           <> show rules)
            else
                let jumpN  = fromIntegral (max 2 (fromMaybe 2 (csCounterJump spec))) :: Word64
                    aheadW = fromIntegral (max 1 (fromMaybe 1 (csKesPeriodsAhead spec))) :: Word
                    newN = if hasCounterJump then realN + jumpN else realN
                    newPeriod = if hasKesBefore then KESPeriod (currentKES + aheadW) else ocertKESPeriod ocert
                    coldKey = if hasColdKey then wrongColdKeyFor (csByteSeed spec) else ksColdSignKey ks
                    sigma = signedDSIGN coldKey (OCertSignable (ocertVkHot ocert) newN newPeriod)
                    newOcert = ocert { ocertN = newN, ocertKESPeriod = newPeriod, ocertSigma = sigma }
                    newBody = body { Praos.hbOCert = newOcert }
                    KESPeriod c0' = newPeriod
                    evol = if currentKES >= c0' then currentKES - c0' else 0
                    kesKey = if hasHotKey then wrongKesKeyFor (csByteSeed spec) else ksKesSignKey ks
                    kesSig = signAtEvolution kesKey evol newBody
                in  Right (CaseResult (HeaderConway (mkShelleyHeader (Praos.Header newBody kesSig)))
                                      MutateErrorPrecedence "reject")
    _ -> Left "error-precedence: captured header is not a Conway header"


-- | Re-sign a real pool1 header's body with OUR KES key but evolved to the
-- WRONG number of steps for the header's KES period: the target evolution is
-- @correctEvol + delta@ (from @kes_evolution_delta@, delta /= 0), while the
-- opcert (period, counter, cold signature, hot vkey) is left valid. The node
-- verifies the KES signature at the expected evolution @t = kp - c0@; a
-- period-bound KES signature produced at any other evolution fails there
-- (InvalidKesSignatureOCERT). Fail-closed: a target evolution below 0 (header
-- period too early to under-evolve), above @maxKESEvolutions@, or beyond the
-- key's usable lifetime returns 'Left' (the driver scores that iteration
-- inconclusive), never a valid header that would be falsely accepted.
applyKesEvolution :: KeySet -> CaseSpec -> Header -> Either String CaseResult
applyKesEvolution ks spec hdr = case hdr of
    HeaderConway shelleyHdr ->
        let praosHdr = shelleyHeaderRaw shelleyHdr
            body = Praos.headerBody praosHdr
            ocert = Praos.hbOCert body
            ourHot = kesHotVerKeyBytes (ksKesSignKey ks)
            theirHot = KES.rawSerialiseVerKeyKES (ocertVkHot ocert)
        in  if ourHot /= theirHot
                then Left "kes-evolution: captured header is not from our pool (hot-key mismatch)"
                else
                    let correctEvol = kesEvolutions (ksSlotsPerKESPeriod ks) (Praos.hbSlotNo body) (ocertKESPeriod ocert)
                        delta = maybe 0 fromInteger (csKesEvoDelta spec) :: Int
                        targetI = fromIntegral correctEvol + delta :: Int
                    in  if delta == 0
                            then Left "kes-evolution unreachable: kes_evolution_delta is 0 (that is the correct evolution, an accepted header)"
                        else if targetI < 0
                            then Left ("kes-evolution unreachable: target evolution " <> show targetI
                                       <> " < 0 (correctEvol=" <> show correctEvol <> "; header period too early to under-evolve by " <> show delta <> ")")
                        else if fromIntegral targetI > ksMaxKESEvo ks
                            then Left ("kes-evolution unreachable: target evolution " <> show targetI
                                       <> " > maxKESEvolutions " <> show (ksMaxKESEvo ks))
                        else case signAtEvolutionMaybe (ksKesSignKey ks) (fromIntegral targetI) body of
                                Nothing   -> Left ("kes-evolution unreachable: KES key exhausted before evolution " <> show targetI)
                                Just sig' -> Right (CaseResult (HeaderConway (mkShelleyHeader (Praos.Header body sig'))) MutateKESKey "reject")
    _ -> Left "kes-evolution: captured header is not a Conway header"

-- | Structural CBOR re-encoding of an otherwise-valid header's wire bytes. The
-- logical header (and therefore its hash + KES signature) is unchanged; only
-- the serialization deviates, so the re-encoded bytes reach the target's header
-- decoder. Every choice is driven by @seed@ so the exact bytes are reproducible.
reEncodeOpcert :: Int -> String -> Maybe Int -> LBS.ByteString -> LBS.ByteString
reEncodeOpcert seed form mTrailing bytes = case form of
    "trailing-bytes" ->
        let n = max 1 (fromMaybe 1 mTrailing)
            extra = BS.pack (take n (randomRs (0, 255) (mkStdGen seed))) :: BS.ByteString
        in bytes <> LBS.fromStrict extra
    "noncanonical-int" -> nonCanonicalOuter bytes
    _ -> case deserialiseFromBytes decodeTerm bytes of
        Left _ -> bytes
        Right (_, term) -> toLazyByteString (encodeTerm (deviateTerm seed form term))

-- | Widen the outermost CBOR length header to a non-canonical 1-byte argument
-- (a genuine non-minimal length encoding that decodes to the same value). Only
-- byte 0 is touched, so this is unambiguous and never corrupts a payload byte.
nonCanonicalOuter :: LBS.ByteString -> LBS.ByteString
nonCanonicalOuter bytes = case LBS.uncons bytes of
    Just (b, rest)
        | ai <= 23 && major <= 6 ->
            LBS.pack [(major `shiftL` 5) .|. 0x18, ai] <> rest
      where
        major = b `shiftR` 5 :: Word8
        ai = b .&. 0x1f :: Word8
    _ -> bytes

-- | Does this Term match the requested structural deviation?
matchesForm :: String -> Term -> Bool
matchesForm form t = case (form, t) of
    ("definite-array", TListI _)      -> True
    ("indefinite-array", TList _)     -> True
    ("extra-map-key", TMap _)         -> True
    ("extra-map-key", TMapI _)        -> True
    ("duplicate-map-key", TMap (_:_)) -> True
    ("duplicate-map-key", TMapI (_:_))-> True
    ("missing-optional-key", TMap ps) -> length ps >= 2
    ("missing-optional-key", TMapI ps)-> length ps >= 2
    _ -> False

-- | Apply the deviation to a single matched node.
deviateNode :: String -> Term -> Term
deviateNode form t = case (form, t) of
    ("definite-array", TListI xs)    -> TList xs
    ("indefinite-array", TList xs)   -> TListI xs
    ("extra-map-key", TMap ps)       -> TMap (ps ++ [(TInt 987654321, TNull)])
    ("extra-map-key", TMapI ps)      -> TMapI (ps ++ [(TInt 987654321, TNull)])
    ("duplicate-map-key", TMap (p:ps))  -> TMap (p : p : ps)
    ("duplicate-map-key", TMapI (p:ps)) -> TMapI (p : p : ps)
    ("missing-optional-key", TMap ps)   -> TMap (init ps)
    ("missing-optional-key", TMapI ps)  -> TMapI (init ps)
    _ -> t

-- | Count nodes matching @form@ (pre-order).
countMatching :: String -> Term -> Int
countMatching form = go
  where
    go t = (if matchesForm form t then 1 else 0) + sum (map go (termChildren t))

termChildren :: Term -> [Term]
termChildren t = case t of
    TList xs    -> xs
    TListI xs   -> xs
    TMap ps     -> concatMap (\(k, v) -> [k, v]) ps
    TMapI ps    -> concatMap (\(k, v) -> [k, v]) ps
    TTagged _ x -> [x]
    _ -> []

-- | Transform the seed-chosen matching node in the Term tree.
deviateTerm :: Int -> String -> Term -> Term
deviateTerm seed form term =
    let n = countMatching form term
    in if n <= 0
        then term
        else let idx = fst (randomR (0, n - 1) (mkStdGen seed))
             in transformNth form idx term

-- | Transform the @target@-th (pre-order, 0-based) matching node.
transformNth :: String -> Int -> Term -> Term
transformNth form target root = snd (walk 0 root)
  where
    walk :: Int -> Term -> (Int, Term)
    walk i t =
        let matchedHere = matchesForm form t
            i' = if matchedHere then i + 1 else i
        in if matchedHere && (i == target)
            then (i', deviateNode form t)
            else recurse i' t
    recurse i t = case t of
        TList xs    -> let (i', xs') = mapAcc i xs in (i', TList xs')
        TListI xs   -> let (i', xs') = mapAcc i xs in (i', TListI xs')
        TMap ps     -> let (i', ps') = mapAccPairs i ps in (i', TMap ps')
        TMapI ps    -> let (i', ps') = mapAccPairs i ps in (i', TMapI ps')
        TTagged w x -> let (i', x') = walk i x in (i', TTagged w x')
        _ -> (i, t)
    mapAcc i [] = (i, [])
    mapAcc i (x:xs) =
        let (i1, x1) = walk i x
            (i2, xs1) = mapAcc i1 xs
        in (i2, x1 : xs1)
    mapAccPairs i [] = (i, [])
    mapAccPairs i ((k, v):ps) =
        let (i1, k1) = walk i k
            (i2, v1) = walk i1 v
            (i3, ps1) = mapAccPairs i2 ps
        in (i3, (k1, v1) : ps1)


-- ===================================================================
-- Family #6: opcert-field CBOR mutation (decoder-level differential)
-- ===================================================================

-- | The named opcert-field CBOR mutations. Each targets ONE opcert field
-- (hot-vkey, counter, KES period, cold-sig) or the opcert array structure, with
-- a CBOR-structure deviation the semantic families (#1-#5) cannot express:
-- wrong value-encoding (negative / bignum where a uint is required), type
-- confusion (counter/period as text or bytes), truncated fixed-width bytes, and
-- missing / duplicate / extra opcert fields. Fed to BOTH implementations' header
-- decoders, a divergence (one accepts, the other rejects) is a decode-leniency
-- finding at the opcert level.
oPCERT_FIELD_MUTATIONS :: [String]
oPCERT_FIELD_MUTATIONS =
    [ "counter-negative"
    , "counter-bignum-oversized"
    , "counter-type-text"
    , "counter-type-bytes"
    , "kesperiod-type-text"
    , "kesperiod-negative"
    , "hot-vkey-truncated"
    , "cold-sig-truncated"
    , "opcert-missing-field"
    , "opcert-duplicate-field"
    , "opcert-extra-field"
    ]

-- | Exported spelling (Haskell top-level identifiers cannot be ALL_CAPS).
oPCERT_FIELD_MUTATIONS_export :: [String]
oPCERT_FIELD_MUTATIONS_export = oPCERT_FIELD_MUTATIONS

-- | Is this Term the operational certificate: a 4-element array
-- @[vkHot(bytes 32), n(uint), c0(uint), sigma(bytes 64)]@? The 32/64 byte widths
-- (KES hot vkey / Ed25519 signature) make it unambiguous within a Praos header.
isOpcertNode :: Term -> Bool
isOpcertNode t = case t of
    TList xs  -> matchOpcert xs
    TListI xs -> matchOpcert xs
    _         -> False
  where
    matchOpcert [TBytes vk, n, c0, TBytes sg] =
        BS.length vk == 32 && isUintTerm n && isUintTerm c0 && BS.length sg == 64
    matchOpcert _ = False

isUintTerm :: Term -> Bool
isUintTerm (TInt i)     = i >= 0
isUintTerm (TInteger i) = i >= 0
isUintTerm _            = False

asBytesTerm :: Term -> BS.ByteString
asBytesTerm (TBytes b) = b
asBytesTerm _          = BS.empty

-- | Apply a named mutation to the opcert's 4 fields.
mutateOpcertFields :: String -> [Term] -> [Term]
mutateOpcertFields m fields = case fields of
    [vk, n, c0, sg] -> case m of
        "counter-negative"         -> [vk, TInt (-1), c0, sg]
        "counter-bignum-oversized" -> [vk, TInteger (18446744073709551616 + 1), c0, sg]
        "counter-type-text"        -> [vk, TString "1", c0, sg]
        "counter-type-bytes"       -> [vk, TBytes (BS.pack [0x01]), c0, sg]
        "kesperiod-type-text"      -> [vk, n, TString "1", sg]
        "kesperiod-negative"       -> [vk, n, TInt (-1), sg]
        "hot-vkey-truncated"       -> [TBytes (BS.take 16 (asBytesTerm vk)), n, c0, sg]
        "cold-sig-truncated"       -> [vk, n, c0, TBytes (BS.take 32 (asBytesTerm sg))]
        "opcert-missing-field"     -> [vk, n, c0]              -- drop sigma
        "opcert-duplicate-field"   -> [vk, n, n, c0, sg]       -- duplicate counter
        "opcert-extra-field"       -> [vk, n, c0, sg, TNull]   -- append a 5th element
        _                          -> fields                    -- "none"/identity
    _ -> fields

-- | Replace the FIRST opcert node in the Term tree with its mutated form,
-- preserving definite/indefinite framing and every other byte of the header.
mutateFirstOpcert :: String -> Term -> Term
mutateFirstOpcert m root = snd (go root)
  where
    go t
        | isOpcertNode t = (True, applyOp t)
        | otherwise      = recurse t
    applyOp (TList xs)  = TList (mutateOpcertFields m xs)
    applyOp (TListI xs) = TListI (mutateOpcertFields m xs)
    applyOp x           = x
    recurse t = case t of
        TList xs    -> let (d, xs') = firstList xs in (d, TList xs')
        TListI xs   -> let (d, xs') = firstList xs in (d, TListI xs')
        TMap ps     -> let (d, ps') = firstPairs ps in (d, TMap ps')
        TMapI ps    -> let (d, ps') = firstPairs ps in (d, TMapI ps')
        TTagged w x -> let (d, x') = go x in (d, TTagged w x')
        _           -> (False, t)
    firstList [] = (False, [])
    firstList (x:xs) =
        let (d, x') = go x
        in if d then (True, x' : xs)
           else let (d2, xs') = firstList xs in (d2, x' : xs')
    firstPairs [] = (False, [])
    firstPairs ((k, v):ps) =
        let (dk, k') = go k
        in if dk then (True, (k', v) : ps)
           else let (dv, v') = go v
                in if dv then (True, (k', v') : ps)
                   else let (d2, ps') = firstPairs ps in (d2, (k', v') : ps')
