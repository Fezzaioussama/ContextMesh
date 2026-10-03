"""Static import direction is enforced without loading application modules."""

from helpers import RepositoryCase


class ArchitectureGateTests(RepositoryCase):
    def test_domain_stdlib_and_domain_imports_pass(self) -> None:
        self.backend(
            "domain/entities.py", "from dataclasses import dataclass\nfrom . import values\n"
        )
        self.assertEqual(self.scan().violations, [])

    def test_application_ports_are_pure_and_domain_import_passes(self) -> None:
        self.backend(
            "application/ports/search.py",
            "from typing import Protocol\nfrom ...domain import entities\n",
        )
        self.assertEqual(self.scan().violations, [])

    def test_domain_cannot_import_application(self) -> None:
        self.backend("domain/model.py", "from context_mesh.application import ports\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_domain_cannot_import_provider_sdk(self) -> None:
        self.backend("domain/model.py", "import openai\n")
        violation = self.scan().violations[0]
        self.assertEqual(violation.line, 1)
        self.assertIn("external dependency openai", violation.message)

    def test_application_cannot_import_unknown_external_dependency(self) -> None:
        self.backend("application/service.py", "from new_dependency import Session\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_application_cannot_import_adapters_by_relative_import(self) -> None:
        self.backend("application/service.py", "from ..adapters.outbound import persistence\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_package_from_import_cannot_hide_a_forbidden_layer(self) -> None:
        self.backend("application/service.py", "from context_mesh import adapters\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_package_from_import_can_select_permitted_domain_layer(self) -> None:
        self.backend("application/service.py", "from context_mesh import domain\n")
        self.assertEqual(self.scan().violations, [])

    def test_adapters_can_import_application_and_external_sdks(self) -> None:
        self.backend(
            "adapters/outbound/models/chat.py",
            "import openai\nfrom context_mesh.application.ports import chat\n",
        )
        self.assertEqual(self.scan().violations, [])

    def test_adapter_cannot_import_bootstrap(self) -> None:
        self.backend("adapters/inbound/http/routes.py", "import context_mesh.bootstrap.settings\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_outbound_adapter_cannot_import_inbound(self) -> None:
        self.backend("adapters/outbound/models/chat.py", "from ...inbound.http import routes\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_bootstrap_can_compose_all_layers(self) -> None:
        self.backend(
            "bootstrap/container.py",
            "import fastapi\nfrom context_mesh.adapters.inbound.http import routes\nfrom context_mesh.application import ports\n",
        )
        self.assertEqual(self.scan().violations, [])

    def test_relative_import_cannot_escape_package_root(self) -> None:
        self.backend("domain/model.py", "from ...outside import anything\n")
        violation = self.scan().violations[0]
        self.assertIn("escapes context_mesh", violation.message)

    def test_unknown_source_layer_fails(self) -> None:
        self.backend("misc/services.py", "value = 1\n")
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_imports_under_type_checking_are_enforced(self) -> None:
        self.backend(
            "domain/model.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import fastapi\n",
        )
        self.assertEqual(self.rules(self.scan()), ["architecture"])

    def test_no_backend_modules_are_explicitly_unverified(self) -> None:
        self.write("README.md", "planned backend")
        self.assertEqual(self.scan().architecture_files, 0)

    def test_broken_backend_is_not_reported_as_absent(self) -> None:
        self.backend("domain/model.py", "return 1\n")
        report = self.scan()
        self.assertEqual(report.architecture_files, 1)
        self.assertEqual(self.rules(report), ["python-syntax"])
