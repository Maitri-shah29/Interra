"""Regression coverage for argument defects in the hosted Kaggle report."""
import unittest
from unittest.mock import AsyncMock, Mock

from agent.fdb_livekit import create_benchmark_tools, normalize_identifier


class FdbToolArgumentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.executor = Mock()
        self.executor.call = AsyncMock(return_value='{"status":"success"}')
        self.tools = create_benchmark_tools(self.executor, lambda **kwargs: lambda fn: fn)

    async def test_apartment_search_forwards_explicit_pet_filter(self):
        await self.tools.search_apartments("Example City", "two", 2100, pets_allowed=True)
        self.executor.call.assert_awaited_once_with(
            "search_apartments", city="Example City", bedrooms=2, max_price=2100.0,
            pets_allowed=True,
        )

    async def test_apartment_search_does_not_invent_pet_filter(self):
        await self.tools.search_apartments("Example City", 3, "2400")
        self.assertNotIn("pets_allowed", self.executor.call.call_args.kwargs)

    async def test_filter_preserves_boolean_number_and_text_types(self):
        for supplied, expected in [
            (True, True), (False, False), ("true", True), ("false", False),
            ("1850", 1850), (1850.0, 1850.0), ("2.5", 2.5), ("balcony", "balcony"),
        ]:
            with self.subTest(value=supplied):
                self.executor.call.reset_mock()
                await self.tools.update_search_filter("example_filter", supplied)
                actual = self.executor.call.call_args.kwargs["value"]
                self.assertEqual(actual, expected)
                self.assertIs(type(actual), type(expected))

    async def test_spelled_identifiers_reach_backend_as_digits_and_letters(self):
        await self.tools.track_order("z q seven")
        self.executor.call.assert_awaited_once_with("track_order", order_id="ZQ7")
        self.executor.call.reset_mock()
        await self.tools.add_to_cart("r eight", "two")
        self.executor.call.assert_awaited_once_with("add_to_cart", product_id="R8", quantity=2)

    async def test_literal_identifiers_preserve_punctuation(self):
        for identifier in ["SKU-42", "order_123", "Example Product", "555-0100", "v777"]:
            self.assertEqual(normalize_identifier(identifier), identifier)

    async def test_invalid_number_never_reaches_backend(self):
        with self.assertRaises(ValueError):
            await self.tools.search_apartments("Example City", "unknown", 2200)
        self.executor.call.assert_not_awaited()
