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

use std::collections::BTreeSet;

use crate::{Hash, NonEmptySet, size::KEY};

/// The public-key hashes a transaction declares it must be signed by.
///
/// Taken from the transaction's `required_signers` field and exposed in the [`TxInfo`](super::tx_info::TxInfo).
/// Importantly, this is the *declared* requirement, not the key hashes that actually produced witnesses.
/// Phase-one validation enforces that the witness set satisfies it.
#[derive(Debug, Default)]
pub struct RequiredSigners(pub BTreeSet<Hash<KEY>>);

impl<'a> From<&'a NonEmptySet<Hash<KEY>>> for RequiredSigners {
    fn from(value: &'a NonEmptySet<Hash<KEY>>) -> Self {
        Self(value.iter().copied().collect())
    }
}
