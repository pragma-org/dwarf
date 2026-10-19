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

use amaru_uplc::{arena::Arena, binder::DeBruijn, term::Term};
use criterion::{Criterion, criterion_group};

use super::utils;

pub fn run(c: &mut Criterion) {
    c.bench_function("fibonacci", |b| {
        b.iter_with_setup(
            || {
                utils::setup_term(|arena: &Arena| {
                    let double_force = Term::var(arena, DeBruijn::new(arena, 1))
                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 1)))
                        .lambda(arena, DeBruijn::zero(arena))
                        .delay(arena)
                        .force(arena)
                        .apply(
                            arena,
                            Term::var(arena, DeBruijn::new(arena, 3))
                                .apply(
                                    arena,
                                    Term::var(arena, DeBruijn::new(arena, 1))
                                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 1)))
                                        .lambda(arena, DeBruijn::zero(arena))
                                        .delay(arena)
                                        .force(arena)
                                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 2))),
                                )
                                .apply(arena, Term::var(arena, DeBruijn::new(arena, 1)))
                                .lambda(arena, DeBruijn::zero(arena))
                                .lambda(arena, DeBruijn::zero(arena)),
                        )
                        .lambda(arena, DeBruijn::zero(arena))
                        .delay(arena)
                        .delay(arena)
                        .force(arena)
                        .force(arena);

                    let if_condition = Term::if_then_else(arena)
                        .force(arena)
                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 3)))
                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 2)))
                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 1)))
                        .apply(arena, Term::unit(arena))
                        .lambda(arena, DeBruijn::zero(arena))
                        .lambda(arena, DeBruijn::zero(arena))
                        .lambda(arena, DeBruijn::zero(arena))
                        .delay(arena)
                        .force(arena);

                    let add = Term::add_integer(arena)
                        .apply(
                            arena,
                            Term::var(arena, DeBruijn::new(arena, 3)).apply(
                                arena,
                                Term::subtract_integer(arena)
                                    .apply(arena, Term::var(arena, DeBruijn::new(arena, 2)))
                                    .apply(arena, Term::integer_from(arena, 1)),
                            ),
                        )
                        .apply(
                            arena,
                            Term::var(arena, DeBruijn::new(arena, 3)).apply(
                                arena,
                                Term::subtract_integer(arena)
                                    .apply(arena, Term::var(arena, DeBruijn::new(arena, 2)))
                                    .apply(arena, Term::integer_from(arena, 2)),
                            ),
                        )
                        .lambda(arena, DeBruijn::zero(arena));

                    double_force
                        .apply(
                            arena,
                            if_condition
                                .apply(
                                    arena,
                                    Term::less_than_equals_integer(arena)
                                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 1)))
                                        .apply(arena, Term::integer_from(arena, 1)),
                                )
                                .apply(
                                    arena,
                                    Term::var(arena, DeBruijn::new(arena, 2)).lambda(arena, DeBruijn::zero(arena)),
                                )
                                .apply(arena, add)
                                .lambda(arena, DeBruijn::zero(arena))
                                .lambda(arena, DeBruijn::zero(arena)),
                        )
                        .apply(arena, Term::var(arena, DeBruijn::new(arena, 1)))
                        .lambda(arena, DeBruijn::zero(arena))
                        .apply(arena, Term::integer_from(arena, 15))
                })
            },
            // Benchmark: only the eval call
            |state| state.exec(),
        )
    });
}

criterion_group!(fibonacci, run);
