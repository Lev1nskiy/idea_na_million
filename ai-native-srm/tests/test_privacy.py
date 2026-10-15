"""Тесты маскирования ПДн (Ст. 4)."""
import logging

import pytest

from core.privacy import PiiLogFilter, mask_payload, mask_pii, text_hash


@pytest.mark.parametrize(
    "raw",
    ["+7 (999) 123-45-67", "89991234567", "8 999 123 45 67"],
)
def test_phone_masked(raw):
    out = mask_pii(f"Позвоните мне {raw} после обеда")
    assert "[PHONE]" in out
    assert "123" not in out


def test_email_masked():
    out = mask_pii("Пишите на ivan.petrov@example.com")
    assert out == "Пишите на [EMAIL]"


def test_serial_masked():
    assert "[SERIAL]" in mask_pii("Мой принтер SN-PXM42-N500101 не включается")
    out = mask_pii("серийный номер ABC123456 указан на корпусе")
    assert "ABC123456" not in out and "[SERIAL]" in out


@pytest.mark.parametrize("raw", ["150 000 ₽", "1 500 000 руб.", "90000 рублей", "50 тыс"])
def test_amount_masked(raw):
    out = mask_pii(f"Мой бюджет {raw} на квартал")
    assert "[AMOUNT]" in out
    assert "000" not in out and "50 тыс" not in out


def test_full_name_masked():
    assert mask_pii("Меня зовут Иванов Иван Иванович") == "Меня зовут [NAME]"
    assert mask_pii("Контакт: Петров П.П.") == "Контакт: [NAME]"


@pytest.mark.parametrize(
    "text",
    [
        "Сколько стоит МФУ PX-M4200 для 10 человек?",
        "Дайте скидку 5% на 3 принтера",
        "Есть ли PX-L3300D в наличии?",
        "Совместимо с 1С:Предприятие 8?",
    ],
)
def test_normal_text_not_broken(text):
    assert mask_pii(text) == text


def test_mask_payload_recurses():
    payload = {"text": "звоните 89991234567", "items": ["a@b.ru", 5], "n": 1}
    out = mask_payload(payload)
    assert out == {"text": "звоните [PHONE]", "items": ["[EMAIL]", 5], "n": 1}


def test_text_hash_stable_and_normalized():
    assert text_hash("Привет  МИР") == text_hash("  привет мир ")
    assert text_hash("a") != text_hash("b")
    assert len(text_hash("a")) == 16


def test_log_filter_masks_pii():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "client %s wrote", ("a@b.ru",), None)
    assert PiiLogFilter().filter(record) is True
    assert record.getMessage() == "client [EMAIL] wrote"


# --- Регрессия: фильтр не должен ломать access-логи uvicorn --------------------------------
def test_access_log_record_formats_with_filter_installed():
    """uvicorn.access сам разбирает record.args: фильтр обязан сохранить кортеж."""
    import logging

    from uvicorn.logging import AccessFormatter

    from core.privacy import install_pii_log_filter

    install_pii_log_filter()
    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/api/health", "1.1", 200),
        None,
    )
    # Формат uvicorn: статус подставляется строкой вида «200 OK», поэтому %s, а не %d.
    text = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert "200 OK" in text and "/api/health" in text


def test_filter_masks_pii_inside_access_log_url():
    """ПДн из URL access-лога (например, телефон в query) не попадают в лог."""
    import logging

    from uvicorn.logging import AccessFormatter

    from core.privacy import PiiLogFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", "/callback?phone=+79991234567", "1.1", 200),
        None,
    )
    PiiLogFilter().filter(record)
    text = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert "+79991234567" not in text and "200 OK" in text


def test_filter_masks_pii_in_args_and_message():
    """ПДн из аргументов и из готового сообщения маскируются, текст остаётся читаемым."""
    import logging

    from core.privacy import PiiLogFilter

    filter_ = PiiLogFilter()
    via_args = logging.LogRecord("t", logging.INFO, __file__, 0, "клиент %s", ("+7 (999) 123-45-67",), None)
    filter_.filter(via_args)
    assert "123-45-67" not in via_args.getMessage() and "[PHONE]" in via_args.getMessage()

    plain = logging.LogRecord("t", logging.INFO, __file__, 0, "почта ivan@example.com", None, None)
    filter_.filter(plain)
    assert "ivan@example.com" not in plain.getMessage() and "[EMAIL]" in plain.getMessage()
