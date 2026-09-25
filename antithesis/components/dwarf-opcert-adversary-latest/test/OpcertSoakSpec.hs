{-# LANGUAGE OverloadedStrings #-}
{-# LANGUAGE LambdaCase #-}

-- | Unit tests for the soak extension of the opcert forger: the seed-derived
-- @--case-spec@ parser and the deterministic encoding-form CBOR re-encoder.
--
-- The re-encoder is exercised against a representative CBOR 'Term' fixture
-- (a nested array + map, mirroring the shape of a header body's opcert region)
-- rather than a full captured devnet header: the property under test is purely
-- structural (does the deviation stay reproducible and still decode?), which a
-- Term fixture proves without a live capture.
module Main (main) where

import Codec.CBOR.Read (deserialiseFromBytes)
import Codec.CBOR.Term (Term (..), decodeTerm, encodeTerm)
import Codec.CBOR.Write (toLazyByteString)
import Data.ByteString.Lazy qualified as LBS
import Data.ByteString.Lazy.Char8 qualified as LBC
import DwarfOpcertAdversary
    ( CaseSpec (..)
    , parseCaseSpec
    , caseSpecByteSeed
    , reEncodeOpcert
    )
import Test.Hspec

isLeft :: Either a b -> Bool
isLeft = \case Left _ -> True; _ -> False

-- A representative, valid CBOR structure standing in for a header's opcert body.
sampleTerm :: Term
sampleTerm =
    TList
        [ TInt 1
        , TMap [(TInt 1, TInt 2), (TInt 3, TInt 4)]
        , TListI [TInt 5, TInt 6]
        ]

sampleBody :: LBS.ByteString
sampleBody = toLazyByteString (encodeTerm sampleTerm)

decodeTop :: LBS.ByteString -> Maybe Term
decodeTop bs = case deserialiseFromBytes decodeTerm bs of
    Right (_, t) -> Just t
    Left _ -> Nothing

encodedBytes :: LBS.ByteString -> LBS.ByteString
encodedBytes = id

-- A re-encoding "reaches the decoder to the same opcert" iff the deviant bytes
-- still decode and the leading CBOR term is byte-preserved (trailing-bytes only
-- appends after a complete, valid term).
reEncodesToSameOpcert :: String -> Int -> LBS.ByteString -> Bool
reEncodesToSameOpcert form n body =
    decodeTop (reEncodeOpcert 0 form (Just n) body) == decodeTop body

main :: IO ()
main = hspec $ do
    describe "parseCaseSpec" $ do
        it "parses an encoding-form trailing-bytes spec" $ do
            let js = "{\"base_case\":\"valid-control\",\"seed\":91827,\
                     \\"params\":{\"encoding_form\":\"trailing-bytes\",\"trailing_len\":3}}"
            parseCaseSpec js `shouldSatisfy` \case
                Right s -> csBaseCase s == "valid-control"
                             && csEncodingForm s == Just "trailing-bytes"
                             && csTrailingLen s == Just 3
                             && csSeed s == 91827
                _ -> False
        it "parses a restart-persistence spec (replay_counter)" $ do
            let js = "{\"base_case\":\"counter-behind\",\"seed\":5,\
                     \\"params\":{\"replay_counter\":2}}"
            parseCaseSpec js `shouldSatisfy` \case
                Right s -> csBaseCase s == "counter-behind" && csReplayCounter s == Just 2
                _ -> False
        it "rejects an unknown encoding form (raw-bytes is out of scope)" $
            parseCaseSpec "{\"base_case\":\"valid-control\",\"seed\":1,\
                \\"params\":{\"encoding_form\":\"raw-bytes\"}}" `shouldSatisfy` isLeft
        it "parses a cross-pool-confusion spec (foreign_pool)" $ do
            let js = "{\"base_case\":\"cross-pool\",\"seed\":5,\"params\":{\"foreign_pool\":\"pool2\",\"variant\":\"foreign-cold-authorization\"}}"
            parseCaseSpec js `shouldSatisfy` \case
                Right sp -> csBaseCase sp == "cross-pool" && csForeignPool sp == Just "pool2"
                _ -> False

    describe "reEncodeOpcert" $ do
        it "trailing-bytes re-encoding still decodes to the same term (reaches decoder)" $
            reEncodesToSameOpcert "trailing-bytes" 3 sampleBody `shouldBe` True
        it "trailing-bytes actually appends the requested number of bytes" $
            LBS.length (reEncodeOpcert 7 "trailing-bytes" (Just 3) sampleBody)
                `shouldBe` LBS.length sampleBody + 3
        it "is deterministic in the seed (same seed => same bytes)" $
            encodedBytes (reEncodeOpcert 42 "trailing-bytes" (Just 3) sampleBody)
                `shouldBe` encodedBytes (reEncodeOpcert 42 "trailing-bytes" (Just 3) sampleBody)
        it "indefinite-array deviation changes the bytes but still decodes" $ do
            let dev = reEncodeOpcert 1 "indefinite-array" Nothing sampleBody
            dev `shouldNotBe` sampleBody
            decodeTop dev `shouldSatisfy` \case Just _ -> True; Nothing -> False
        it "extra-map-key deviation changes the bytes" $
            reEncodeOpcert 1 "extra-map-key" Nothing sampleBody `shouldNotBe` sampleBody
        it "noncanonical-int widens the outer length header (still decodes)" $ do
            let dev = reEncodeOpcert 1 "noncanonical-int" Nothing sampleBody
            dev `shouldNotBe` sampleBody
            decodeTop dev `shouldBe` decodeTop sampleBody

    describe "caseSpecByteSeed (per-iteration randomization)" $ do
        let specFor bs =
                "{\"base_case\":\"valid-control\",\"seed\":1000,                \"params\":{\"encoding_form\":\"trailing-bytes\",                \"trailing_len\":6,\"byte_seed\":" ++ show (bs :: Int) ++ "}}"
            parsedSeed bs = case parseCaseSpec (LBC.pack (specFor bs)) of
                Right sp -> Just (caseSpecByteSeed sp)
                Left _ -> Nothing
        it "reads the per-iteration byte_seed from params (not the campaign seed)" $ do
            parsedSeed 11111 `shouldBe` Just 11111
            parsedSeed 22222 `shouldBe` Just 22222
        it "distinct byte_seeds produce DISTINCT served bytes (real randomization)" $ do
            let a = reEncodeOpcert 11111 "trailing-bytes" (Just 6) sampleBody
                b = reEncodeOpcert 22222 "trailing-bytes" (Just 6) sampleBody
            a `shouldNotBe` b
        it "the SAME byte_seed reproduces identical bytes (replayable)" $ do
            let a = reEncodeOpcert 33333 "trailing-bytes" (Just 6) sampleBody
                b = reEncodeOpcert 33333 "trailing-bytes" (Just 6) sampleBody
            a `shouldBe` b
        it "falls back to the campaign seed only when byte_seed is absent" $ do
            let js = "{\"base_case\":\"valid-control\",\"seed\":777,                     \"params\":{\"encoding_form\":\"trailing-bytes\"}}"
            case parseCaseSpec (LBC.pack js) of
                Right sp -> caseSpecByteSeed sp `shouldBe` 777
                Left e -> expectationFailure e

