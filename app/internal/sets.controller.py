# Controller: Sets manager UI.
#
# Wires the head/menu/body view scriptlets and points the controller-proxy
# at the `data` service in this set. All operations (clone, create, pull,
# switch, remove, update_deps) reach `data.task.py` via the proxy.

set = "internal"
service = "data"
debug = False
head = "sets_head"
menu = "sets_menu"
body = "sets_body"
name = "Sets"
