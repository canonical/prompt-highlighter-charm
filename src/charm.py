#!/usr/bin/env python3
import os
from jinja2 import Environment, FileSystemLoader
from ops import CharmBase, main, ActiveStatus, BlockedStatus

class PromptHighlighterCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)

    def _on_config_changed(self, event):
        env_type = self.config.get("environment-type")
        color = self.config.get("prompt-color")
        enable_zsh = self.config.get("enable-zsh")

        # 1. Write Python runner script to target path
        try:
            jinja_env = Environment(loader=FileSystemLoader("templates"))
            template = jinja_env.get_template("prompt.py.j2")
            rendered_script = template.render(environment_type=env_type, prompt_color=color)
            
            script_path = "/usr/local/bin/juju_dynamic_prompt.py"
            with open(script_path, "w") as f:
                f.write(rendered_script)
            os.chmod(script_path, 0o755)
        except Exception as e:
            self.unit.status = BlockedStatus(f"Failed script generation: {str(e)}")
            return

        # 2. Append hook wrapper to global Bash configuration
        bash_snippet = "\nset_bash_prompt() { PS1=$(/usr/local/bin/juju_dynamic_prompt.py); }\nPROMPT_COMMAND=set_bash_prompt\n"
        self._append_profile("/etc/bash.bashrc", bash_snippet)

        # 3. Append hook wrapper to global Zsh configuration
        if enable_zsh:
            zsh_snippet = "\nsetopt prompt_subst\nset_zsh_prompt() { PROMPT='$(/usr/local/bin/juju_dynamic_prompt.py)'; }\nautoload -Uz add-zsh-hook\nadd-zsh-hook precmd set_zsh_prompt\n"
            self._append_profile("/etc/zsh/zshrc", zsh_snippet)

        self.unit.status = ActiveStatus(f"Prompt set to {env_type} ({color})")

    def _append_profile(self, file_path, snippet):
        if os.path.exists(file_path):
            with open(file_path, "r") as f:
                if "juju_dynamic_prompt.py" in f.read():
                    return
        with open(file_path, "a") as f:
            f.write(snippet)

if __name__ == "__main__":
    main(PromptHighlighterCharm)
