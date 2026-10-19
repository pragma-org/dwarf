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

use chumsky::{Parser, prelude::*};

use super::{term, types::Extra, utils::comments, version};
use crate::{binder::DeBruijn, program::Program};

pub fn parser<'a>() -> impl Parser<'a, &'a str, &'a Program<'a, DeBruijn>, Extra<'a>> {
    text::keyword("program")
        .padded()
        .ignore_then(version::parser().padded())
        .then(term::parser().padded())
        .delimited_by(just('('), just(')'))
        .padded()
        .padded_by(comments())
        .then_ignore(end())
        .map_with(|(version, term), e| {
            let state = e.state();

            Program::new(state.arena, version, term)
        })
}
