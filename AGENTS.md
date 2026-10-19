# AGENTS.md

## Modo solo lectura ante preguntas

- Si el usuario hace una pregunta o el mensaje contiene un signo de interrogación (`?`), entra en **modo solo lectura**: investiga lo necesario (leer, buscar, ejecutar consultas) y responde. No modifiques ficheros, configuración ni servicios.
- Ese modo dura hasta que el usuario pida explícitamente un cambio ("hazlo", "corrige", "añade"…).

## Response style

- **No unsolicited proposals.** Do not keep offering next actions, options, or menus at the end of answers. Report what was asked and stop.
- **Be spare.** Keep answers short. No recaps, no restating the request, no filler.
- **Do only what was asked.** Do not expand scope. Perform the requested task or order; do not take adjacent actions on your own.
