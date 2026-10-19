// Copyright 2026 PRAGMA
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use std::{collections::BTreeMap, ops::Deref};

use crate::{MemoizedTransactionOutput, OutputReference, TransactionInput};

/// A subset of the UTxO set.
///
/// Maps from a `TransactionInput` to a `MemoizedTransactionOutput`
pub struct Utxos(BTreeMap<TransactionInput, MemoizedTransactionOutput>);

impl<'a> Utxos {
    /// Resolve an input to the output it references, returning an [`OutputReference`]
    ///
    ///
    /// Returns `None` when the input cannot be found in the UTxO slice.
    pub fn resolve_input(&'a self, input: &'a TransactionInput) -> Option<OutputReference<'a>> {
        self.0.get(input).map(|utxo| OutputReference { input, output: utxo })
    }
}

impl Deref for Utxos {
    type Target = BTreeMap<TransactionInput, MemoizedTransactionOutput>;

    fn deref(&self) -> &Self::Target {
        &self.0
    }
}

impl From<BTreeMap<TransactionInput, MemoizedTransactionOutput>> for Utxos {
    fn from(value: BTreeMap<TransactionInput, MemoizedTransactionOutput>) -> Self {
        Self(value)
    }
}
