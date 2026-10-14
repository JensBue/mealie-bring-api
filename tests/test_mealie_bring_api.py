import asyncio
import copy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from source.bring_handler import BringHandler
from source.ingredient import Ingredient
from source.logger_mixin import LoggerMixin
from source.mealie_bring_api import (
    DEFAULT_MOVE_INGREDIENTS_DEBOUNCE_SECONDS,
    SHOPPING_LIST_EVENT_SUPPRESSION_SECONDS,
    Flask,
    MealieBringAPI,
)
from source.mealie_handler import MealieHandler

_REAL_CREATE_APP = MealieBringAPI._create_app


@pytest.fixture
def mock_event_loop():
    loop = MagicMock(spec=asyncio.AbstractEventLoop)
    loop.run_until_complete = MagicMock()
    loop.stop = MagicMock()
    return loop


@pytest.fixture
def mock_bring_handler():
    handler = MagicMock(spec=BringHandler)
    handler.add_items = AsyncMock()
    handler.notify_users_about_changes_in_list = AsyncMock()
    handler.logout = AsyncMock()
    return handler


@pytest.fixture
def mock_flask_app():
    app = MagicMock(spec=Flask)
    app.register_blueprint = MagicMock()
    app.run = MagicMock()
    return app


@pytest.fixture
def mock_mealie_handler():
    return MagicMock(spec=MealieHandler)


@pytest.fixture
def mealie_app(monkeypatch, mock_event_loop, mock_bring_handler, mock_flask_app, mock_mealie_handler):
    monkeypatch.delenv("SHOPPING_LIST_DEBOUNCE_SECONDS", raising=False)
    monkeypatch.setattr(MealieBringAPI, "_create_logger", lambda self: LoggerMixin())
    monkeypatch.setattr(MealieBringAPI, "_create_event_loop", lambda self: mock_event_loop)
    monkeypatch.setattr(MealieBringAPI, "_create_bring_handler", lambda self, loop: mock_bring_handler)
    monkeypatch.setattr(MealieBringAPI, "_create_app", lambda self: mock_flask_app)
    monkeypatch.setattr("source.mealie_bring_api.MealieHandler", lambda: mock_mealie_handler)

    return MealieBringAPI()


@pytest.mark.parametrize(
    ("env_value", "expected_value"),
    [
        (None, DEFAULT_MOVE_INGREDIENTS_DEBOUNCE_SECONDS),
        ("", DEFAULT_MOVE_INGREDIENTS_DEBOUNCE_SECONDS),
        ("invalid", DEFAULT_MOVE_INGREDIENTS_DEBOUNCE_SECONDS),
        ("0", 1),
        ("-5", 1),
        ("60", 60),
    ],
)
def test_get_move_ingredients_debounce_seconds(monkeypatch, env_value, expected_value):
    if env_value is None:
        monkeypatch.delenv("SHOPPING_LIST_DEBOUNCE_SECONDS", raising=False)
    else:
        monkeypatch.setenv("SHOPPING_LIST_DEBOUNCE_SECONDS", env_value)

    result = MealieBringAPI._get_move_ingredients_debounce_seconds()

    assert result == expected_value


def test_process_recipe_data_with_enabled_amount(mealie_app, example_request):
    def __eq__(self, other):
        return self.name == other.name and self.specification == other.specification

    Ingredient.__eq__ = __eq__
    expected_ingredients = [
        Ingredient(name="Berry", specification="1 Gram"),
        Ingredient(name="Apple", specification="1"),
        Ingredient(name="Salt", specification="5 Spoons"),
        Ingredient(name="Pepper", specification="4 Grams"),
        Ingredient(name="Water", specification="6 Liters"),
        Ingredient(name="Sugar", specification="2 Kilograms"),
        Ingredient(name="Chocolate", specification=""),
    ]

    result = mealie_app.process_recipe_data(example_request)

    assert result == expected_ingredients


def test_process_recipe_data_with_disabled_amount(mealie_app, example_request):
    example_request["content"]["settings"]["disable_amount"] = True

    with patch("source.mealie_bring_api.IngredientWithAmountsDisabled.from_raw_data") as mock_from_raw_data:
        unparsed_ingredients = mealie_app._extract_ingredients_data_from_recipe_data(
            example_request["content"]["recipe_ingredient"]
        )

        result = mealie_app.process_recipe_data(example_request)

        assert mock_from_raw_data.call_count == len(unparsed_ingredients)
        assert len(result) == mock_from_raw_data.call_count


def test_process_recipe_data_with_household_ingredient(mealie_app, example_request):
    unparsed_ingredients_ = mealie_app._extract_ingredients_data_from_recipe_data(
        example_request["content"]["recipe_ingredient"]
    )
    target_ingredient = unparsed_ingredients_[0]

    def mock_in_household(ingredient_data):
        return ingredient_data == target_ingredient

    with patch("source.mealie_bring_api.Ingredient.in_household", side_effect=mock_in_household):
        with patch("source.mealie_bring_api.Ingredient.from_raw_data") as mock_from_raw_data:
            mealie_app.process_recipe_data(example_request)

            expected_total_with_amount = len([i for i in unparsed_ingredients_ if i.get("food") is not None])
            assert mock_from_raw_data.call_count == expected_total_with_amount - 1


def test_process_recipe_data_ignores_empty_parsed_ingredient(mealie_app, example_request, caplog):
    result_without_empty_ingredient = mealie_app.process_recipe_data(copy.deepcopy(example_request))
    emtpy_ingredient_data = {
        "display": "",
        "food": None,
        "note": "",
        "quantity": 0.0,
        "unit": None,
    }
    example_request["content"]["recipe_ingredient"].append(emtpy_ingredient_data)

    result_with_empty_ingredient = mealie_app.process_recipe_data(example_request)

    assert result_with_empty_ingredient == result_without_empty_ingredient
    assert f"Ignoring empty ingredient {emtpy_ingredient_data}" in caplog.text


def test_add_ingredients_to_bring_empty_list(mealie_app):
    mealie_app._add_ingredients_to_bring([])

    mealie_app.bring_handler.add_items.assert_not_called()
    mealie_app.bring_handler.notify_users_about_changes_in_list.assert_not_called()


def test_add_ingredients_to_bring_with_ingredients(mealie_app, first_ingredient, second_ingredient):
    ingredients = [first_ingredient, second_ingredient]

    mealie_app._add_ingredients_to_bring(ingredients)

    mealie_app.bring_handler.add_items.assert_called_once_with(ingredients)
    mealie_app.bring_handler.notify_users_about_changes_in_list.assert_called_once()


def test_move_ingredients_from_shopping_list_to_bring(mealie_app):
    items_on_shopping_list = [{"id": "1"}, {"id": "2"}]
    mealie_app.mealie_handler.get_items_on_shopping_list.return_value = items_on_shopping_list

    with (
        patch("source.mealie_bring_api.Ingredient.from_raw_data") as mock_from_raw_data,
        patch.object(mealie_app, "_add_ingredients_to_bring") as mock_add_ingredients_to_bring,
    ):
        mealie_app._move_ingredients_from_shopping_list_to_bring()

    assert mock_from_raw_data.call_count == len(items_on_shopping_list)
    mock_add_ingredients_to_bring.assert_called_once()
    mealie_app.mealie_handler.delete_items_from_shopping_list.assert_called_once_with(items_on_shopping_list)


def test_move_ingredients_from_empty_shopping_list_does_not_call_bring_or_delete(mealie_app, caplog):
    mealie_app.mealie_handler.get_items_on_shopping_list.return_value = []

    with patch.object(mealie_app, "_add_ingredients_to_bring") as mock_add_ingredients_to_bring:
        mealie_app._move_ingredients_from_shopping_list_to_bring()

    mock_add_ingredients_to_bring.assert_not_called()
    mealie_app.mealie_handler.delete_items_from_shopping_list.assert_not_called()
    assert "Shopping list is empty, nothing to move" in caplog.text


def test_schedule_move_ingredients_ignores_events_during_active_export(mealie_app, caplog):
    mealie_app.move_export_is_running = True

    with patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls:
        mealie_app._schedule_move_ingredients_from_shopping_list()

    mock_timer_cls.assert_not_called()
    assert "Ignoring shopping list event caused by active export" in caplog.text


def test_schedule_move_ingredients_ignores_events_during_suppression_window(mealie_app, caplog):
    mealie_app.ignore_shopping_list_events_until = 10

    with (
        patch("source.mealie_bring_api.time.monotonic", return_value=9),
        patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls,
    ):
        mealie_app._schedule_move_ingredients_from_shopping_list()

    mock_timer_cls.assert_not_called()
    assert "Ignoring shopping list event caused by active export" in caplog.text


def test_schedule_move_ingredients_processes_user_change_after_suppression_window(mealie_app):
    mealie_app.ignore_shopping_list_events_until = 10

    with (
        patch("source.mealie_bring_api.time.monotonic", return_value=11),
        patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls,
    ):
        mock_timer_instance = MagicMock()
        mock_timer_cls.return_value = mock_timer_instance

        mealie_app._schedule_move_ingredients_from_shopping_list()

    mock_timer_cls.assert_called_once_with(2, mealie_app._run_debounced_move_ingredients_from_shopping_list)
    mock_timer_instance.start.assert_called_once()


def test_schedule_move_ingredients_starts_timer(mealie_app):
    with patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls:
        mock_timer_instance = MagicMock()
        mock_timer_cls.return_value = mock_timer_instance

        mealie_app._schedule_move_ingredients_from_shopping_list()

    mock_timer_cls.assert_called_once_with(2, mealie_app._run_debounced_move_ingredients_from_shopping_list)
    mock_timer_instance.start.assert_called_once()
    assert mealie_app.move_debounce_timer is mock_timer_instance


def test_schedule_move_ingredients_uses_configured_debounce_seconds(mealie_app):
    mealie_app.move_ingredients_debounce_seconds = 60

    with patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls:
        mock_timer_cls.return_value = MagicMock()

        mealie_app._schedule_move_ingredients_from_shopping_list()

    mock_timer_cls.assert_called_once_with(60, mealie_app._run_debounced_move_ingredients_from_shopping_list)


def test_schedule_move_ingredients_cancels_previous_pending_timer(mealie_app):
    previous_timer = MagicMock()
    mealie_app.move_debounce_timer = previous_timer

    with patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls:
        mock_timer_cls.return_value = MagicMock()
        mealie_app._schedule_move_ingredients_from_shopping_list()

    previous_timer.cancel.assert_called_once()


def test_single_shopping_list_change_runs_one_export(mealie_app):
    with (
        patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls,
        patch.object(mealie_app, "_move_ingredients_from_shopping_list_to_bring") as mock_move,
    ):
        mock_timer = MagicMock()
        mock_timer_cls.return_value = mock_timer

        mealie_app._schedule_move_ingredients_from_shopping_list()
        mock_timer_cls.call_args.args[1]()

    mock_move.assert_called_once()


def test_multiple_shopping_list_changes_within_debounce_run_one_export(mealie_app):
    timers = []

    def create_timer(_seconds, callback):
        timer = MagicMock()
        timer.callback = callback
        timers.append(timer)
        return timer

    with (
        patch("source.mealie_bring_api.threading.Timer", side_effect=create_timer),
        patch.object(mealie_app, "_move_ingredients_from_shopping_list_to_bring") as mock_move,
    ):
        mealie_app._schedule_move_ingredients_from_shopping_list()
        mealie_app._schedule_move_ingredients_from_shopping_list()
        timers[1].callback()

    timers[0].cancel.assert_called_once()
    mock_move.assert_called_once()


def test_delete_events_after_export_do_not_schedule_second_export(mealie_app):
    items_on_shopping_list = [{"id": "1"}, {"id": "2"}, {"id": "3"}, {"id": "4"}]
    mealie_app.mealie_handler.get_items_on_shopping_list.return_value = items_on_shopping_list

    with (
        patch("source.mealie_bring_api.Ingredient.from_raw_data", side_effect=lambda item: item),
        patch.object(mealie_app, "_add_ingredients_to_bring"),
        patch("source.mealie_bring_api.threading.Timer") as mock_timer_cls,
        patch("source.mealie_bring_api.time.monotonic", side_effect=[100, 101, 101, 101, 101]),
    ):

        def delete_items(_items):
            mealie_app._schedule_move_ingredients_from_shopping_list()
            mealie_app._schedule_move_ingredients_from_shopping_list()
            mealie_app._schedule_move_ingredients_from_shopping_list()
            mealie_app._schedule_move_ingredients_from_shopping_list()

        mealie_app.mealie_handler.delete_items_from_shopping_list.side_effect = delete_items

        mealie_app._run_debounced_move_ingredients_from_shopping_list()
        mealie_app._schedule_move_ingredients_from_shopping_list()

    mealie_app.mealie_handler.delete_items_from_shopping_list.assert_called_once_with(items_on_shopping_list)
    mock_timer_cls.assert_not_called()
    assert mealie_app.ignore_shopping_list_events_until == 100 + SHOPPING_LIST_EVENT_SUPPRESSION_SECONDS


def test_run_debounced_move_ingredients_clears_timer_and_moves(mealie_app):
    mealie_app.move_debounce_timer = MagicMock()

    with patch.object(mealie_app, "_move_ingredients_from_shopping_list_to_bring") as mock_move:
        mealie_app._run_debounced_move_ingredients_from_shopping_list()

    assert mealie_app.move_debounce_timer is None
    mock_move.assert_called_once()


def test_handle_stop_signal_stops_loop_and_logs_out(mealie_app, monkeypatch):
    with patch("source.mealie_bring_api.sys.exit") as mock_exit:
        mealie_app._handle_stop_signal(signal_number=2, _frame=None)

        mealie_app.bring_handler.logout.assert_called_once()
        mealie_app.loop.stop.assert_called_once()
        mock_exit.assert_called_once_with(0)


def test_handle_stop_signal_flushes_pending_debounced_move(mealie_app):
    pending_timer = MagicMock()
    mealie_app.move_debounce_timer = pending_timer

    with (
        patch("source.mealie_bring_api.sys.exit"),
        patch.object(mealie_app, "_move_ingredients_from_shopping_list_to_bring") as mock_move,
    ):
        mealie_app._handle_stop_signal(signal_number=2, _frame=None)

    pending_timer.cancel.assert_called_once()
    mock_move.assert_called_once()
    assert mealie_app.move_debounce_timer is None


def test_mealplan_note_text_ignores_recipes_and_invalid_notes():
    assert MealieBringAPI._mealplan_note_text({"title": "  TK-Pizza  "}) == "  TK-Pizza  "
    assert MealieBringAPI._mealplan_note_text({"title": "Pommes", "recipeId": "recipe-1"}) is None
    assert MealieBringAPI._mealplan_note_text({"title": "Pommes", "recipe": {"id": "recipe-1"}}) is None
    assert MealieBringAPI._mealplan_note_text({"title": "   "}) is None
    assert MealieBringAPI._mealplan_note_text({"title": None}) is None
    assert MealieBringAPI._mealplan_note_text({}) is None


def test_mealplan_notes_are_added_as_mealie_shopping_items(mealie_app):
    from datetime import date

    mealie_app.mealie_handler.get_mealplan_entries.return_value = [
        {"date": "2026-10-19", "title": "Pommes"},
        {"date": "2026-10-20", "title": "Baguette"},
        {"date": "2026-10-21", "title": "Schnitzel", "recipeId": "recipe-id"},
    ]
    mealie_app.mealie_handler.get_target_shopping_list_id.return_value = "target-list"
    mealie_app.mealie_handler.get_shopping_item_names.return_value = ["Salz"]
    mealie_app.mealie_handler.add_shopping_list_notes.return_value = 2

    result = mealie_app._add_mealplan_notes_to_shopping_list(date(2026, 10, 19), date(2026, 10, 24))

    assert result == {"added": 2, "skipped_duplicates": 0}
    mealie_app.mealie_handler.add_shopping_list_notes.assert_called_once_with("target-list", ["Pommes", "Baguette"])
    mealie_app.bring_handler.add_items.assert_not_awaited()
    mealie_app.bring_handler.notify_users_about_changes_in_list.assert_not_awaited()


def test_mealplan_notes_exclude_out_of_range_and_empty_notes(mealie_app):
    from datetime import date

    mealie_app.mealie_handler.get_mealplan_entries.return_value = [
        {"date": "2026-10-18", "title": "Vorher"},
        {"date": "2026-10-25", "title": "Nachher"},
        {"date": "2026-10-20", "title": "   "},
        {"date": "2026-10-21", "title": "Im Zeitraum"},
    ]
    mealie_app.mealie_handler.add_shopping_list_notes.return_value = 1

    result = mealie_app._add_mealplan_notes_to_shopping_list(date(2026, 10, 19), date(2026, 10, 24))

    assert result == {"added": 1, "skipped_duplicates": 0}
    mealie_app.mealie_handler.add_shopping_list_notes.assert_called_once_with(
        mealie_app.mealie_handler.get_target_shopping_list_id.return_value, ["Im Zeitraum"]
    )


def test_mealplan_note_duplicates_are_skipped_case_insensitively(mealie_app):
    from datetime import date

    mealie_app.mealie_handler.get_mealplan_entries.return_value = [
        {"date": "2026-10-19", "title": "Pommes"},
        {"date": "2026-10-20", "title": " pommes "},
        {"date": "2026-10-21", "title": "BAGUETTE"},
    ]
    mealie_app.mealie_handler.get_shopping_item_names.return_value = ["  baguette  "]
    mealie_app.mealie_handler.add_shopping_list_notes.return_value = 1

    result = mealie_app._add_mealplan_notes_to_shopping_list(date(2026, 10, 19), date(2026, 10, 24))

    assert result == {"added": 1, "skipped_duplicates": 1}
    mealie_app.mealie_handler.add_shopping_list_notes.assert_called_once_with(
        mealie_app.mealie_handler.get_target_shopping_list_id.return_value, ["Pommes"]
    )


def test_existing_shopping_items_are_only_read_and_never_changed(mealie_app):
    from datetime import date

    mealie_app.mealie_handler.get_mealplan_entries.return_value = [{"date": "2026-10-19", "title": "Pommes"}]
    mealie_app.mealie_handler.get_shopping_item_names.return_value = [" Pommes "]
    mealie_app.mealie_handler.add_shopping_list_notes.return_value = 0

    result = mealie_app._add_mealplan_notes_to_shopping_list(date(2026, 10, 19), date(2026, 10, 24))

    assert result == {"added": 0, "skipped_duplicates": 1}
    mealie_app.mealie_handler.add_shopping_list_notes.assert_called_once_with(
        mealie_app.mealie_handler.get_target_shopping_list_id.return_value, []
    )
    mealie_app.mealie_handler.delete_items_from_shopping_list.assert_not_called()
    mealie_app.bring_handler.add_items.assert_not_awaited()


def test_mealplan_note_range_route_uses_visible_dates_and_returns_counts(mealie_app):
    from datetime import date
    from flask import Flask

    mealie_app.mealie_handler.mealie_is_setup = True
    mealie_app._add_mealplan_notes_to_shopping_list = MagicMock(return_value={"added": 3, "skipped_duplicates": 1})
    with patch("source.mealie_bring_api.app", Flask("test-mealie-bring")):
        test_app = _REAL_CREATE_APP(mealie_app)
    response = test_app.test_client().post(
        "/add-mealplan-notes-to-shopping-list?start_date=2026-10-19&end_date=2026-10-24"
    )

    assert response.status_code == 200
    assert response.json == {"added": 3, "skipped_duplicates": 1}
    mealie_app._add_mealplan_notes_to_shopping_list.assert_called_once_with(date(2026, 10, 19), date(2026, 10, 24))


def test_mealplan_note_range_validation_route(mealie_app):
    from flask import Flask

    mealie_app.mealie_handler.mealie_is_setup = True
    with patch("source.mealie_bring_api.app", Flask("test-mealie-bring")):
        test_app = _REAL_CREATE_APP(mealie_app)
    client = test_app.test_client()
    assert client.post("/add-mealplan-notes-to-shopping-list").status_code == 400
    assert client.post(
        "/add-mealplan-notes-to-shopping-list?start_date=2026-10-19&end_date=2026-10-14"
    ).status_code == 400
