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

use std::time::{Duration, SystemTime};

use crate::{EraHistory, EraHistoryError, GlobalParameters, Slot};

/// An interval of time using POSIX time
///
///
/// Time is a difficult, and heavily documented, challenge on Cardano.
/// To maintain deterministic transaction validation,
/// Cardano uses a validity interval which makes a transaction only valid from slot X to slot Y.
///
/// By default, the validity interval is unbounded, meaning the transcation could always be valid.
///
/// One wrinkle that this causes is that while Ouroboros uses slots to handle time, Plutus uses POSIX time.
/// See [`TimeRange::new`] for more information on converting from a validity interval from Ouroboros to a Plutus TimeRange.
#[derive(Debug)]
pub struct TimeRange {
    pub lower_bound: Option<SystemTime>,
    pub upper_bound: Option<SystemTime>,
}

impl TimeRange {
    /// Construct a new [`TimeRange`] given a slot interval
    ///
    /// Slot lengths can change based at hard forks, so it is not safe to count slots.
    /// Conversion is handled by the provided `era_history`, which depends on the correct `network` (to determine the `GlobalParamters`)
    ///
    /// There are a few cases that would lead to an `EraHistoryError`:
    /// - `EraHistoryError::PastTimeHorizon`:
    ///   If a bound is too far in the future, we cannot be sure that a hardfork will occur that will change timings.
    ///   The time horizon is the "stability window", which can take up to 3k/f
    /// - `EraHistory::InvalidEraHistory`:
    ///   One of the bounds cannot be found in any era in the `EraHistory`, so we do not know the slot length
    ///   and thus cannot convert to POSIX time. This is typically going to be the result of a user error (incorrect era history)
    pub fn new(
        valid_from_slot: Option<Slot>,
        valid_to_slot: Option<Slot>,
        tip: &Slot,
        era_history: &EraHistory,
        global_parameters: &GlobalParameters,
    ) -> Result<Self, EraHistoryError> {
        // TODO: Use 'SystemTime' for system_start in GlobalParameters
        let system_start = SystemTime::UNIX_EPOCH + Duration::from_millis(global_parameters.system_start);
        let lower_bound =
            valid_from_slot.map(|slot| era_history.slot_to_posix_time(slot, *tip, system_start)).transpose()?;
        let upper_bound =
            valid_to_slot.map(|slot| era_history.slot_to_posix_time(slot, *tip, system_start)).transpose()?;

        Ok(Self { lower_bound, upper_bound })
    }
}
