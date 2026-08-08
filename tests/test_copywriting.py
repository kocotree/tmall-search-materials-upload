from upload_search_materials.copywriting import generate_and_validate_copy


class FakeProvider:
    def __init__(self, output):
        self.output = output
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return self.output


def test_unsupported_claim_is_manual_review():
    provider = FakeProvider(
        {"title": "KK树女童防晒水杯", "description": "适合女童日常使用，具备UPF50+防晒能力。"}
    )

    result = generate_and_validate_copy(
        source={"品牌": "KK树", "品类": "水杯", "卖点1": "便携"},
        provider=provider,
        prohibited_terms=["UPF50+"],
        generated_at="2026-07-17T10:00:00+08:00",
    )

    assert result.status == "needs_manual_review"
    assert "PROHIBITED_TERM" in result.reason_codes
    assert "UNSUPPORTED_CLAIM" in result.reason_codes


def test_only_allowed_source_fields_are_sent_to_provider():
    provider = FakeProvider(
        {"title": "KK树儿童便携水杯", "description": "便携设计，适合有明确儿童属性的日常饮水场景。"}
    )

    result = generate_and_validate_copy(
        source={
            "品牌": "KK树",
            "品类": "水杯",
            "卖点1": "便携",
            "商品标题": "儿童水杯",
            "内部利润": "90%",
        },
        provider=provider,
        prohibited_terms=[],
        generated_at="2026-07-17T10:00:00+08:00",
    )

    assert "内部利润" not in provider.requests[0].source_fields
    assert result.source_fields["商品标题"] == "儿童水杯"
    assert result.status == "valid"


def test_missing_gender_source_blocks_generated_gender():
    provider = FakeProvider(
        {"title": "KK树女童便携水杯", "description": "便携设计，适合女童日常饮水和外出携带。"}
    )

    result = generate_and_validate_copy(
        source={"品牌": "KK树", "品类": "水杯", "卖点1": "便携"},
        provider=provider,
        prohibited_terms=[],
        generated_at="2026-07-17T10:00:00+08:00",
    )

    assert "UNSUPPORTED_CLAIM" in result.reason_codes


def test_length_rules_are_deterministic():
    provider = FakeProvider({"title": "超" * 31, "description": "太短"})

    result = generate_and_validate_copy(
        source={"品牌": "KK树", "品类": "水杯"},
        provider=provider,
        prohibited_terms=[],
        generated_at="2026-07-17T10:00:00+08:00",
    )

    assert result.reason_codes == ["TITLE_TOO_LONG", "DESCRIPTION_LENGTH_INVALID"]


def test_generation_time_and_raw_output_are_auditable():
    output = {"title": "KK树便携水杯", "description": "便携水杯设计，满足日常携带和饮水使用需求。"}
    provider = FakeProvider(output)

    result = generate_and_validate_copy(
        source={"品牌": "KK树", "品类": "水杯", "卖点1": "便携"},
        provider=provider,
        prohibited_terms=[],
        generated_at="2026-07-17T10:00:00+08:00",
    )

    assert result.generated_at == "2026-07-17T10:00:00+08:00"
    assert result.raw_output == output
