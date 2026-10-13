"""Synthetic section markers for assembly tests; no production prompt content."""

PROMPT_DOCUMENTS = {
    key: f"test-section:{key}"
    for key in (
        "preview_system", "preview_writing_rules", "preview_data_rules",
        "competition_male", "competition_female", "competition_female_2026_2027",
        "competition_futsal",
    )
}
