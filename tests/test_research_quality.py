from switchcrew.research import ResearchProvider, research_snippets


async def test_whole_word_matching_avoids_support_matching_port():
    provider = ResearchProvider(
        ["https://example.org"], fetch=lambda url: "Customer support hours."
    )
    result = await provider.investigate("port", ["Find port evidence"])
    assert result["status"] == "blocked"


async def test_criteria_aware_ranking_deduplicates_and_keeps_markdown_bounded():
    text = "\n".join(
        [
            "# Gateway",
            "Discord Gateway intents select event delivery.",
            "Discord Gateway intents select event delivery.",
            "```text",
            "GUILD_MEMBERS is a privileged intent requiring approval.",
            "GUILD_PRESENCES is a privileged intent.",
            "```",
            "Discord Gateway documents event delivery and intents.",
        ]
    )
    calls = []

    def fetch(url):
        calls.append(url)
        return text

    provider = ResearchProvider(["https://example.org", "https://example.org"], fetch=fetch)
    result = await provider.investigate("Discord Gateway intents", ["Find privileged intents"])
    excerpts = result["evidence"][0]["excerpts"]
    assert any("privileged" in excerpt for excerpt in excerpts)
    assert len(excerpts) == len(set(excerpts))
    assert calls == ["https://example.org"]
    assert all("```" not in excerpt and len(excerpt) <= 600 for excerpt in excerpts)
    assert result["criterion_evidence"][0]["status"] == "human_review_required"
    assert result["criterion_evidence"][0]["source_urls"] == ["https://example.org"]


def test_long_code_or_paragraph_never_becomes_one_huge_excerpt():
    text = "Gateway " + "intent " * 1000
    snippets = research_snippets(text)
    assert len(snippets) > 1
    assert all(len(snippet) <= 600 for snippet in snippets)
    assert all(
        not snippet.startswith("```") for snippet in research_snippets("```python\nfoo\n```")
    )


async def test_provider_error_strings_do_not_echo_sensitive_upstream_messages():
    def fetch(url):
        raise ValueError("Authorization: Bearer sensitive-token")

    result = await ResearchProvider(["https://example.org"], fetch=fetch).investigate(
        "Gateway", ["Find Gateway information"]
    )
    assert result["errors"] == [{"url": "https://example.org", "error": "ValueError"}]
