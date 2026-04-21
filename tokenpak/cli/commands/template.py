"""template command — team shared prompt templates."""

from __future__ import annotations

import json
import os
import sys

try:
    import click

    @click.group("template")
    def template_cmd():
        """Team shared prompt template commands."""
        pass

    @template_cmd.command("list")
    @click.option("--team", is_flag=True, default=True, help="List team templates (default)")
    @click.option("--tag", default=None, help="Filter by tag")
    @click.option("--role", default="member", help="Actor role (member|admin)")
    @click.option("--server", default=None, help="Team server URL (default: http://localhost:8766)")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON")
    def template_list(team, tag, role, server, as_json):
        """List team templates.

        Example:
            tokenpak template list --team
            tokenpak template list --tag summarise
        """
        import urllib.error
        import urllib.request

        server_url = server or os.environ.get("TOKENPAK_SERVER", "http://localhost:8766")
        url = f"{server_url}/v1/team/templates"
        if tag:
            url += f"?tag={tag}"

        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                result = json.loads(resp.read())
        except urllib.error.URLError as exc:
            click.echo(f"✗ Failed to reach team server: {exc}", err=True)
            sys.exit(1)

        templates = result.get("templates", [])

        # Filter by role visibility
        if role != "admin":
            templates = [t for t in templates if t.get("role_required", "member") == "member"]

        if tag:
            templates = [t for t in templates if tag in t.get("tags", [])]

        if as_json:
            click.echo(json.dumps(templates, indent=2))
            return

        if not templates:
            click.echo("No templates found.")
            return

        click.echo(f"Team templates ({len(templates)}):")
        for t in templates:
            tags_str = f" [{', '.join(t.get('tags', []))}]" if t.get("tags") else ""
            desc = f" — {t['description']}" if t.get("description") else ""
            click.echo(f"  {t['name']}{tags_str}{desc}")

    @template_cmd.command("use")
    @click.argument("name")
    @click.option("--team", is_flag=True, default=True, help="Use a team template (default)")
    @click.option(
        "--var", multiple=True, metavar="KEY=VALUE", help="Template variable substitutions"
    )
    @click.option("--role", default="member", help="Actor role (member|admin)")
    @click.option("--server", default=None, help="Team server URL (default: http://localhost:8766)")
    def template_use(name, team, var, role, server):
        """Fetch and render a team template.

        Variables are substituted using {{key}} syntax:

            tokenpak template use summarise --var content="Hello world"

        """
        import urllib.error
        import urllib.request

        server_url = server or os.environ.get("TOKENPAK_SERVER", "http://localhost:8766")

        # Fetch all templates and find by name
        try:
            with urllib.request.urlopen(f"{server_url}/v1/team/templates", timeout=10) as resp:
                result = json.loads(resp.read())
        except urllib.error.URLError as exc:
            click.echo(f"✗ Failed to reach team server: {exc}", err=True)
            sys.exit(1)

        templates = {t["name"]: t for t in result.get("templates", [])}
        if name not in templates:
            click.echo(f"✗ Template {name!r} not found.", err=True)
            sys.exit(1)

        template = templates[name]

        # RBAC check
        if template.get("role_required") == "admin" and role != "admin":
            click.echo(f"✗ Template {name!r} requires admin role.", err=True)
            sys.exit(1)

        # Parse variables
        variables = {}
        for v in var:
            if "=" in v:
                k, val = v.split("=", 1)
                variables[k.strip()] = val.strip()

        # Render
        content = template["content"]
        for k, val in variables.items():
            content = content.replace(f"{{{{{k}}}}}", val)

        click.echo(content)

    @template_cmd.command("create")
    @click.argument("name")
    @click.option("--content", required=True, help="Template content (use {{var}} for variables)")
    @click.option("--description", default="", help="Short description")
    @click.option("--tag", multiple=True, help="Tags")
    @click.option("--role-required", default="member", help="Minimum role to use (member|admin)")
    @click.option("--created-by", default=None, help="Creator name (defaults to $USER)")
    @click.option("--actor-role", default="admin", help="Your role (must be admin)")
    @click.option("--server", default=None, help="Team server URL (default: http://localhost:8766)")
    def template_create(
        name, content, description, tag, role_required, created_by, actor_role, server
    ):
        """Create a new team template (admin only).

        Example:
            tokenpak template create summarise \\
                --content "Summarise this in 3 bullets: {{content}}" \\
                --description "Bullet summariser"
        """
        import urllib.error
        import urllib.request

        server_url = server or os.environ.get("TOKENPAK_SERVER", "http://localhost:8766")
        created_by = created_by or os.environ.get("USER", "unknown")

        payload = json.dumps(
            {
                "name": name,
                "content": content,
                "created_by": created_by,
                "actor_role": actor_role,
                "description": description,
                "tags": list(tag),
                "role_required": role_required,
            }
        ).encode()

        req = urllib.request.Request(
            f"{server_url}/v1/team/templates",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                json.loads(resp.read())
            click.echo(f"✓ Template {name!r} created")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            click.echo(f"✗ Error {exc.code}: {body}", err=True)
            sys.exit(1)
        except urllib.error.URLError as exc:
            click.echo(f"✗ Failed to reach team server: {exc}", err=True)
            sys.exit(1)

    @template_cmd.command("delete")
    @click.argument("name")
    @click.option("--actor-role", default="admin", help="Your role (must be admin)")
    @click.option("--server", default=None, help="Team server URL (default: http://localhost:8766)")
    def template_delete(name, actor_role, server):
        """Delete a team template (admin only)."""
        import urllib.error
        import urllib.request

        server_url = server or os.environ.get("TOKENPAK_SERVER", "http://localhost:8766")
        payload = json.dumps({"name": name, "actor_role": actor_role}).encode()
        req = urllib.request.Request(
            f"{server_url}/v1/team/templates/{name}",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="DELETE",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                json.loads(resp.read())
            click.echo(f"✓ Template {name!r} deleted")
        except urllib.error.URLError as exc:
            click.echo(f"✗ Failed to reach team server: {exc}", err=True)
            sys.exit(1)

except ImportError:
    pass
