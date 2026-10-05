"""Argument normalizers, regression-tested on outputs from the 2026-10-01 Kaggle trace.

Each "observed" value below is what GPT-4.1 mini sent in that run and the
official exact-match scorer rejected (docs/results/kaggle-20261001).
"""
import unittest
from unittest.mock import AsyncMock, Mock

from agent.fdb_livekit import (
    create_benchmark_tools,
    normalize_bill_type,
    normalize_commute_mode,
    normalize_currency,
    normalize_doc_type,
    normalize_filter_name,
    normalize_identifier,
    normalize_place,
    normalize_spoken_date,
    strip_trailing_noun,
)


class SpokenDateTests(unittest.TestCase):
    def test_observed_iso_dates_become_month_day(self):
        for observed, expected in [
            ("2023-08-20", "August 20"), ("2024-02-02", "February 2"),
            ("2023-11-05", "November 5"), ("2024-04-10", "April 10"),
            ("2023-12-12", "December 12"), ("08-20", "August 20"),
        ]:
            with self.subTest(observed=observed):
                self.assertEqual(normalize_spoken_date(observed), expected)

    def test_spoken_variants_drop_ordinals_and_years(self):
        for spoken, expected in [
            ("October 14th", "October 14"), ("Oct. 14", "October 14"),
            ("the 14th of October", "October 14"), ("14 October", "October 14"),
            ("October fourteenth", "October 14"), ("October twenty first", "October 21"),
            ("on October 14, 2026", "October 14"), ("sept 9", "September 9"),
            ("October 14", "October 14"),
        ]:
            with self.subTest(spoken=spoken):
                self.assertEqual(normalize_spoken_date(spoken), expected)

    def test_text_that_is_not_one_calendar_date_is_unchanged(self):
        for text in ["next Friday", "tomorrow", "2023-13-40", "", "mid October"]:
            with self.subTest(text=text):
                self.assertEqual(normalize_spoken_date(text), text)


class IdentifierTests(unittest.TestCase):
    def test_observed_hyphen_spelled_ids_are_joined(self):
        for observed, expected in [
            ("p-5-2", "P52"), ("k-2", "K2"), ("v-4-4", "V44"), ("d-e-l-i-v", "DELIV"),
            ("a-b-c", "ABC"), ("k dash two", "K2"), ("q.r.7", "QR7"),
        ]:
            with self.subTest(observed=observed):
                self.assertEqual(normalize_identifier(observed), expected)

    def test_spaced_groups_and_repeats_are_joined(self):
        for spoken, expected in [
            ("b o b one two", "BOB12"), ("ZQ 12", "ZQ12"), ("double seven", "77"),
            ("x triple one", "X111"), ("r eight", "R8"),
        ]:
            with self.subTest(spoken=spoken):
                self.assertEqual(normalize_identifier(spoken), expected)

    def test_incomplete_repeat_is_left_alone(self):
        self.assertEqual(normalize_identifier("seven double"), "seven double")


class EnumTests(unittest.TestCase):
    def test_document_types_use_backend_spelling(self):
        for observed, expected in [
            ("driver's license", "driver_license"), ("Driver License", "driver_license"),
            ("drivers licence", "driver_license"), ("pass", "passport"),
            ("Passport number", "passport"), ("ID card", "id_card"), ("visa", "visa"),
        ]:
            with self.subTest(observed=observed):
                self.assertEqual(normalize_doc_type(observed), expected)

    def test_unknown_document_type_is_kept(self):
        self.assertEqual(normalize_doc_type("residence permit"), "residence_permit")

    def test_currency_names_become_iso_codes(self):
        for spoken, expected in [
            ("usd", "USD"), ("euros", "EUR"), ("US dollars", "USD"),
            ("British pounds", "GBP"), ("yen", "JPY"), ("won", "KRW"), ("chf", "CHF"), ("Canadian dollars", "CAD"),
            ("Swiss franc", "CHF"), ("doubloons", "doubloons"),
        ]:
            with self.subTest(spoken=spoken):
                self.assertEqual(normalize_currency(spoken), expected)

    def test_redundant_nouns_are_removed(self):
        self.assertEqual(strip_trailing_noun("brokerage account", "account"), "brokerage")
        self.assertEqual(strip_trailing_noun("silver card", "credit card", "card"), "silver")
        self.assertEqual(strip_trailing_noun("account", "account"), "account")
        self.assertEqual(normalize_bill_type("Phone Plan bill"), "phone_plan")
        self.assertEqual(normalize_bill_type("electricity payments"), "electricity")

    def test_commute_modes_map_to_backend_values(self):
        for spoken, expected in [
            ("drive", "driving"), ("by bike", "biking"), ("cycling", "biking"),
            ("public transit", "transit"), ("bus", "transit"), ("walk", "walking"),
            ("Walking", "walking"), ("ferry", "ferry"),
        ]:
            with self.subTest(spoken=spoken):
                self.assertEqual(normalize_commute_mode(spoken), expected)

    def test_place_wording_is_kept_and_ordinals_become_digits(self):
        self.assertEqual(normalize_place("my  house"), "my house")
        self.assertEqual(normalize_place("the bakery on ninth"), "the bakery on 9th")
        self.assertEqual(normalize_place("Eleventh Avenue"), "11th Avenue")
        self.assertEqual(normalize_place("twenty first street"), "21st street")

    def test_spelled_address_numbers_become_digits(self):
        # Observed in the 2026-10-04 LLM replay: house numbers stayed as words.
        for spoken, expected in [
            ("one zero one Main Street", "101 Main Street"),
            ("five hundred Central Ave", "500 Central Ave"),
            ("one twenty three Elm Road", "123 Elm Road"),
            ("forty-two Pine Lane", "42 Pine Lane"),
            ("twelve hundred Oak Street", "1200 Oak Street"),
        ]:
            with self.subTest(spoken=spoken):
                self.assertEqual(normalize_place(spoken), expected)

    def test_lone_number_words_keep_their_meaning(self):
        self.assertEqual(normalize_place("the one on Main"), "the one on Main")
        self.assertEqual(normalize_place("One Market Plaza"), "One Market Plaza")


class FilterNameTests(unittest.TestCase):
    def test_rent_and_budget_bounds_use_the_search_parameter(self):
        # Observed in the 2026-10-04 LLM replay: "max price" was sent as max_rent.
        for spoken, expected in [
            ("max_rent", "max_price"), ("Max Rent", "max_price"),
            ("maximum budget", "max_price"), ("budget", "max_price"),
            ("min-rent", "min_price"), ("max_price", "max_price"),
        ]:
            with self.subTest(spoken=spoken):
                self.assertEqual(normalize_filter_name(spoken), expected)

    def test_pet_and_bedroom_synonyms_use_the_search_parameter(self):
        self.assertEqual(normalize_filter_name("pet friendly"), "pets_allowed")
        self.assertEqual(normalize_filter_name("number of bedrooms"), "bedrooms")

    def test_other_filters_stay_in_the_users_words(self):
        self.assertEqual(normalize_filter_name("Min Bedrooms"), "min_bedrooms")
        self.assertEqual(normalize_filter_name("preferred neighborhood"), "preferred_neighborhood")
        self.assertEqual(normalize_filter_name("parking"), "parking")


class ToolWrapperNormalizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = Mock()
        self.executor.call = AsyncMock(return_value='{"status":"success"}')
        self.tools = create_benchmark_tools(self.executor, lambda **kwargs: lambda fn: fn)

    def sent(self):
        return self.executor.call.call_args

    async def test_flight_search_sends_spoken_date(self):
        await self.tools.search_flights(" Example City ", "2026-10-14")
        self.executor.call.assert_awaited_once_with(
            "search_flights", destination="Example City", date="October 14"
        )

    async def test_identity_update_normalizes_type_and_number(self):
        await self.tools.update_identity_doc("driver's license", "x-9-9")
        self.executor.call.assert_awaited_once_with(
            "update_identity_doc", doc_type="driver_license", doc_number="X99"
        )

    async def test_booking_normalizes_spelled_flight_id(self):
        await self.tools.book_flight("Example Person", "f l one two")
        self.executor.call.assert_awaited_once_with(
            "book_flight", passenger_name="Example Person", flight_id="FL12"
        )

    async def test_finance_tools_use_codes_and_bare_keywords(self):
        await self.tools.get_exchange_rate("two hundred", "Swiss francs", "usd")
        self.assertEqual(self.sent().kwargs, {
            "amount": 200.0, "from_currency": "CHF", "to_currency": "USD",
        })
        await self.tools.modify_autopay("water bill", "Brokerage account")
        self.assertEqual(self.sent().kwargs, {"bill_type": "water", "source_account": "brokerage"})
        await self.tools.get_card_benefits("Silver Card")
        self.assertEqual(self.sent().kwargs, {"card_type": "silver"})

    async def test_commute_keeps_place_wording(self):
        await self.tools.calculate_commute("my house", "the bakery on ninth", "bike")
        self.assertEqual(self.sent().kwargs, {
            "origin_address": "my house", "destination_address": "the bakery on 9th",
            "mode": "biking",
        })

    async def test_filter_name_matches_search_parameters(self):
        await self.tools.update_search_filter("Max Rent", "1900")
        self.assertEqual(self.sent().kwargs, {"filter_name": "max_price", "value": 1900})
        await self.tools.update_search_filter("Has Parking", "yes")
        self.assertEqual(self.sent().kwargs, {"filter_name": "has_parking", "value": "yes"})

    async def test_commute_writes_spoken_house_numbers_as_digits(self):
        await self.tools.calculate_commute("one zero one Main Street", "the office", "drive")
        self.assertEqual(self.sent().kwargs, {
            "origin_address": "101 Main Street", "destination_address": "the office",
            "mode": "driving",
        })

    async def test_product_search_forwards_only_given_options(self):
        await self.tools.search_products("Desk lamps")
        self.assertEqual(self.sent().kwargs, {"query": "Desk lamps"})
        await self.tools.search_products("desk lamps", "40", "Lighting")
        self.assertEqual(self.sent().kwargs, {
            "query": "desk lamps", "max_price": 40.0, "category": "lighting",
        })


if __name__ == "__main__":
    unittest.main()
