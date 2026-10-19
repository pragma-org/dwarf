// Copyright 2025 PRAGMA
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

use std::convert::Infallible;

use thiserror::Error;

#[derive(Error, Debug)]
pub enum FlatEncodeError {
    #[error("Overflow detected, cannot fit {byte} in {num_bits} bits.")]
    Overflow { byte: u8, num_bits: usize },
    #[error("Buffer is not byte aligned")]
    BufferNotByteAligned,
    #[error("Cannot encode BLS12-381 constants")]
    BlsElementNotSupported,
    #[error(transparent)]
    EncodeCbor(#[from] minicbor::encode::Error<Infallible>),
}
