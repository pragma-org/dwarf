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

use amaru_uplc::{arena::Arena, term::Term};
use criterion::{Criterion, criterion_group};

use super::utils;

pub fn run(c: &mut Criterion) {
    c.bench_function("add_integer", |b| {
        b.iter_with_setup(
            || {
                utils::setup_term(|arena: &Arena| {
                    Term::add_integer(arena)
                        .apply(arena, Term::integer_from(arena, 1))
                        .apply(arena, Term::integer_from(arena, 3))
                })
            },
            // Benchmark: only the eval call
            |state| state.exec(),
        )
    });
}

criterion_group!(add_integer, run);
