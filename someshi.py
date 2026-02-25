''' memory shit
from elvis.memory import Memory
from elvis.memory import create_memory_manager

if __name__ == "__main__":
    manager = create_memory_manager()

    manager.delete_memory("4ed69d16-0102-49b6-9af7-394e6f89d85d")
'''

from elvis.memory import Memory
from elvis.memory import create_memory_manager


if __name__ == "__main__":
    manager = create_memory_manager()

    m = Memory(
        content = "Pang likes the color green",
        user_id="1",
    )

    manager.save_memory(m)