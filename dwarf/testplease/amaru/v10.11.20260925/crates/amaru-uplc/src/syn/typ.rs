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

use chumsky::prelude::*;

use super::types::{Extra, MapExtra};
use crate::typ::Type;

pub fn parser<'a>() -> impl Parser<'a, &'a str, &'a Type<'a>, Extra<'a>> {
    recursive(|rec_typ| {
        choice((
            // integer
            text::keyword("integer").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::integer(state.arena)
            }),
            // bool
            text::keyword("bool").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::bool(state.arena)
            }),
            // bytestring
            text::keyword("bytestring").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::byte_string(state.arena)
            }),
            // string
            text::keyword("string").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::string(state.arena)
            }),
            // pair
            text::keyword("pair")
                .padded()
                .ignore_then(rec_typ.clone().padded())
                .then(rec_typ.clone().padded())
                .delimited_by(just('('), just(')'))
                .map_with(|(fst_type, snd_type), e: &mut MapExtra<'a, '_>| {
                    let state = e.state();

                    Type::pair(state.arena, fst_type, snd_type)
                }),
            // list
            text::keyword("list")
                .padded()
                .ignore_then(rec_typ.clone().padded())
                .delimited_by(just('('), just(')'))
                .map_with(|typ, e: &mut MapExtra<'a, '_>| {
                    let state = e.state();

                    Type::list(state.arena, typ)
                }),
            // array
            text::keyword("array")
                .padded()
                .ignore_then(rec_typ.clone().padded())
                .delimited_by(just('('), just(')'))
                .map_with(|typ, e: &mut MapExtra<'a, '_>| {
                    let state = e.state();

                    Type::array(state.arena, typ)
                }),
            // data
            text::keyword("data").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::data(state.arena)
            }),
            // unit
            text::keyword("unit").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::unit(state.arena)
            }),
            // g1
            text::keyword("bls12_381_G1_element").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::g1(state.arena)
            }),
            // g2
            text::keyword("bls12_381_G2_element").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::g2(state.arena)
            }),
            // value
            text::keyword("value").ignored().map_with(|_, e: &mut MapExtra<'a, '_>| {
                let state = e.state();

                Type::value(state.arena)
            }),
        ))
        .boxed()
    })
}
