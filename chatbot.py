from typing import List
from collections.abc import Iterable

from langgraph.func import entrypoint, task
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.runnables.config import RunnableConfig
from langgraph.types import StreamWriter
from rich.pretty import pprint

from elvis.config import Config
from elvis.data import create_checkpointer
from elvis.tools import call_tool, get_available_tools
from elvis.memory import create_memory_manager, Memory
from elvis.models import create_llm 

MEMORY_TEMPLATE = """
[MEMORY]
Content: {content}
Importance: {importance}/10
[/MEMORY]
""".strip()

MESSAGE_TEMPLATE = """
[MESSAGE]
Content: {content}
Role: {role}
[/MESSAGE]
""".strip()

MEMORY_PROMPT_TEMPLATE = """
Use the following part of a conversation between Elvis (Home Assistant AI) and client to decide
if you should save any new information about the client.
Memories with an importance rating of 3 or below should not be saved.

Messages:
[MESSAGE]
{messages}
[/MESSAGE]

Existing memories:
[MEMORIES]
{memories}
[/MEMORIES]

Memory Guidelines:

1. Only save information that is factual, verifiable, or relevant to household management, peoples' names and personal preferences.
2. Do not save trivial or ephemeral details (e.g., casual greetings, jokes, or fleeting comments).
3. Prioritize information that can improve future responses, planning, or personalization (e.g., user preferences, schedules, appliance instructions).
4. Assign higher importance to information that is recurring, critical, or directly affects household routines.
5. Do not store sensitive information unless explicitly relevant to household management and user consent is implied.
6. Merge with existing memories if the new information updates or clarifies them, rather than creating duplicate entries.
7. Ignore information that is ambiguous, speculative, or cannot be acted upon.
8. Only save if the resulting importance rating is 4 or higher.

Instructions:

- Record new significant information using the `save_memory` tool.
- Only use one tool at a time **very important
- Do not save any information deemed insignificant (importance of 3 or below)
- Pay special attention to user preferences, schedules, and names
- Importance values should be between 1 and 10

Reply with "No new memory" if no new information should be saved
"""

ASSISTANT_PROMPT = """
Your name is Elvis. You are a home assistant AI designed to:
- Respond in a neutral, technical tone.
- Provide accurate information and reasoning.
- Use information from:
    1. Household memories (vector database)
    2. Personal information about each user
    3. Tools for searching websites online
- No role-play, jokes, or conversational fillers.
- Be concise, factual, and reasoning-focused.

## Existing Knowledge About Your Client
[MEMORIES]
{memories}
[/MEMORIES]

If you don't know the client's name start by asking for it.
"""

ASSISTANT_CHAT_TEMPLATE = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            ASSISTANT_PROMPT,
        ),
        MessagesPlaceholder(variable_name="messages"),
    ]
)

def add_messages(previous: List[BaseMessage], new_messages: List[BaseMessage]) -> List[BaseMessage]:

    # Ensure we don't modify the original previous list
    combined = previous.copy() if previous else []

    # Append new messages
    combined.extend(new_messages)

    return combined

def get_buffer_string(
    messages : List[BaseMessage],
    human_prefix : str = "Human",
    ai_prefix : str = "AI"
) -> str:
    
    buffer_strings = []

    for m in messages:
        if isinstance(m, HumanMessage):
            prefix=human_prefix
        elif isinstance(m, AIMessage):
            prefix=ai_prefix
        else:
            prefix=""

        buffer_strings.append(f"{prefix}: {m.content}")

    return "\n".join(buffer_strings)

chat_llm = create_llm(Config.CHAT_MODEL)
tool_llm = create_llm(Config.TOOL_MODEL)

@task
def load_memories(messages : List[BaseMessage], user_id : str) -> List[Memory]:
    conversation = get_buffer_string(messages)
    conversation = conversation[:1000]
    return create_memory_manager().retrieve_memories(
        conversation, user_id, k=Config.Memory.MAX_RECALL_COUNT
    )

@task
def generate_response(messages : List[BaseMessage], memories : List[Memory], writer : StreamWriter):
    memories = [
        MEMORY_TEMPLATE.format(content=m.content, importance=m.importance) for m in memories
    ]

    content = ""
    prompt_messages = ASSISTANT_CHAT_TEMPLATE.format_messages(
        messages=messages, memories="\n".join(memories)
    )

    for chunk in chat_llm.stream(prompt_messages):
        content += chunk.content
        writer(chunk.content)

    return AIMessage(content)

@task
def save_new_memory(messages : List[BaseMessage], user_id : str):
    existing_memories = create_memory_manager().find_all_memories(user_id)
    memory_texts = [
        MEMORY_TEMPLATE.format(content=m.content, importance=m.importance) for m in existing_memories
    ]
    message_texts = [
        MESSAGE_TEMPLATE.format(
            content=m.content, role="client" if isinstance(m, HumanMessage) else "assistant"
        )
        for m in messages[-2:]
    ]

    prompt = MEMORY_PROMPT_TEMPLATE.format(
        messages="\n".join(message_texts),
        memories="\n".join(memory_texts),
    )

    llm_with_tools = tool_llm.bind_tools(get_available_tools())
    llm_response = llm_with_tools.invoke([HumanMessage(prompt)])

    if not llm_response.tool_calls:
        return
    assert len(llm_response.tool_calls) == 1, "Only one tool call expected"
    call_tool(llm_response.tool_calls[0])

@entrypoint(checkpointer=create_checkpointer())
def chat_workflow(
    messages : List[BaseMessage], previous, config : RunnableConfig
) -> List[BaseMessage]:
    if previous is not None:
        messages = add_messages(previous, messages)
    user_id = config["configurable"].get("user_id")
    memories = load_memories(messages, user_id).result()

    print("Existing memories: ")
    pprint(memories)

    response = generate_response(messages, memories).result()

    save_new_memory(messages, user_id).result()

    messages = add_messages(messages, [response])
    return entrypoint.final(value=messages, save=messages)

def ask_chatbot(messages: List[BaseMessage], config) -> Iterable[str]:
    for _, chunk in chat_workflow.stream(
        messages, 
        config, 
        stream_mode=["custom"]
    ):
        yield chunk