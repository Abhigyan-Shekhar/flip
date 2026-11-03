from switchcrew.memory import MemoryResult
from switchcrew.memory_acceptance import verify_shared_memory


async def test_acceptance_requires_observed_persistence_and_real_cross_client_recall():
    shared, created = {}, []

    class Client:
        project = "github.com/example/app/acceptance/test"

        def __init__(self):
            created.append(self)

        async def observe(self, user, answer, role, session):
            shared["answer"] = answer
            shared["producer"] = (role, session)
            return MemoryResult(persisted=True)

        async def prime(self, role, session):
            return MemoryResult(context="Ready")

        async def recall(self, query, role, session):
            shared["consumer"] = (role, session)
            return MemoryResult(context=shared["answer"])

    result = await verify_shared_memory(Client)
    assert result["status"] == "PASS"
    assert len(created) == 2
    assert shared["consumer"] != shared["producer"]
    assert "acceptance-" not in str(result)  # No synthetic decision text in the public report.
