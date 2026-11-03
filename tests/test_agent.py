from berend import agent, store
from conftest import tool_call


def test_tool_loop_parallel_calls_streaming_and_cited_sources(fake_llm):
    store.add_document("If payments-api is Degraded, check the pull secret.", "file:///payments.md",
                       "payments.md", "local")
    store.add_document("Keycloak keys rotate every 90 days.", "file:///keycloak.md", "keycloak.md", "local")
    fake_llm.script = [
        [tool_call("search_knowledge_base", {"query": "payments degraded"}),
         tool_call("search_knowledge_base", {"query": "pull secret"})],
        "According to payments.md, check the **pull secret**.",
    ]
    streamed, tools_seen = [], []
    ans = agent.answer("why is payments degraded?", on_text=streamed.append,
                       on_tool=lambda n, a: tools_seen.append(n))

    assert ans.text.startswith("According to payments.md")
    assert ans.tools_used == ["search_knowledge_base", "search_knowledge_base"]
    assert tools_seen == ans.tools_used
    assert len(streamed) > 1 and streamed[-1] == ans.text
    tool_msgs = [m for m in fake_llm.rounds[1] if isinstance(m, dict) and m.get("role") == "tool"]
    assert len(tool_msgs) == 2 and "pull secret" in tool_msgs[0]["content"]
    assert [u for _, u in ans.sources] == ["file:///payments.md"]      # only what it cited


def test_last_round_forces_answer_without_tools(fake_llm, monkeypatch):
    monkeypatch.setattr(agent.config, "MAX_TOOL_ROUNDS", 1)
    seen_tools = []
    real = fake_llm.chat

    def spy(messages, **kw):
        seen_tools.append(kw.get("tools"))
        yield from real(messages, **kw)
    monkeypatch.setattr(agent._client, "chat", spy)
    fake_llm.script = [[tool_call("search_knowledge_base", {"query": "x"})], "done"]
    assert agent.answer("q").text == "done"
    assert seen_tools[0] and seen_tools[1] is None


def test_cited_sources_fallback():
    src = [("a.md", "u1"), ("b.md", "u2"), ("a.md", "u1")]
    assert agent.cited_sources(src, "see b.md") == [("b.md", "u2")]
    assert agent.cited_sources(src, "no idea") == [("a.md", "u1"), ("b.md", "u2")]
