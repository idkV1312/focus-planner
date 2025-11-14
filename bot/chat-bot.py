import asyncio
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, UTC
from typing import List, Optional, Dict

from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, BotStarted

logging.basicConfig(level=logging.INFO)

TOKEN = "f9LHodD0cOIfjiXNMUNZ8JbFdlw6yam3hPU0FoPgAzTGx_puqI19dGg-MBb81wmDjeSKacPy1UfAoXyyr3GL" 
DATA_PATH = "focus_data.json"

bot = Bot(TOKEN)
dp = Dispatcher()


@dataclass
class Task:
    id: str
    title: str
    minutes: int = 45
    priority: int = 2
    deadline: Optional[str] = None
    done: bool = False
    created: str = datetime.now(UTC).isoformat()


@dataclass
class Settings:
    tone: str = "gentle"   
    style: str = "breath"  


@dataclass
class Storage:
    tasks: List[Task]
    settings: Settings


def _default_storage() -> Storage:
    return Storage(
        tasks=[
            Task(id="seed1", title="Подготовить отчёт", minutes=60, priority=1),
            Task(id="seed2", title="Встреча с командой", minutes=30, priority=2),
            Task(id="seed3", title="Прогулка", minutes=45, priority=3),
        ],
        settings=Settings(),
    )


def load_store() -> Storage:
    if not os.path.exists(DATA_PATH):
        save_store(_default_storage())
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        tasks = [Task(**t) for t in raw.get("tasks", [])]
        settings = Settings(**raw.get("settings", {}))
        return Storage(tasks=tasks, settings=settings)
    except Exception:
        return _default_storage()


def save_store(store: Storage):
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "tasks": [asdict(t) for t in store.tasks],
            "settings": asdict(store.settings),
        }, f, ensure_ascii=False, indent=2)


store = load_store()


def uid() -> str:
    return os.urandom(5).hex()


def is_today(dstr: Optional[str]) -> bool:
    if not dstr:
        return True
    try:
        d = datetime.strptime(dstr, "%Y-%m-%d").date()
        return d == datetime.now().date()
    except Exception:
        return False


def fmt_hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def greedy_today(tasks: List[Task], max_minutes: int = 360) -> List[Task]:
    pool = [t for t in tasks if not t.done and is_today(t.deadline)]
    pool.sort(key=lambda t: (t.priority, t.created))
    left = max_minutes
    plan = []
    for t in pool:
        est = t.minutes or 45
        if est <= left:
            plan.append(t)
            left -= est
        if left <= 0:
            break
    return plan


def render_plan(plan: List[Task]) -> str:
    if not plan:
        return "Задач на сегодня нет 🎉"
    cur = datetime.now().replace(second=0, microsecond=0)
    lines = ["📅 *План на сегодня:*"]
    for t in plan:
        start = cur
        end = cur + timedelta(minutes=t.minutes or 45)
        lines.append(
            f"• {t.title}\n  {fmt_hm(start)}–{fmt_hm(end)} · p{t.priority} · {t.minutes} мин (id:{t.id})"
        )
        cur = end
    return "\n".join(lines)


def render_history(tasks: List[Task], q: str = "") -> str:
    lst = sorted(tasks, key=lambda t: t.created, reverse=True)
    if q:
        lst = [t for t in lst if q.lower() in t.title.lower()]
    if not lst:
        return "История пуста 📭"
    lines = ["🗂 *История:*"]
    for t in lst:
        done = "✔️" if t.done else "—"
        dl = f"дедлайн: {t.deadline} · " if t.deadline else ""
        lines.append(f"{done} {t.title} ({dl}p{t.priority} · {t.minutes} мин) id:{t.id}")
    return "\n".join(lines)


def smart_subtasks(goal: str) -> List[str]:
    return [
        f"Уточнить метрику успеха для: {goal}",
        f"Разбить {goal} на 3 этапа и оценки времени",
        f"Сделать план на неделю и назначить дедлайны",
    ]


def parse_nl(s: str) -> Task:
    import re
    now = datetime.now()
    deadline = None

    mapping = {"сегодня": 0, "завтра": 1, "послезавтра": 2}
    for k, offset in mapping.items():
        if k in s.lower():
            deadline = (now + timedelta(days=offset)).strftime("%Y-%m-%d")
            break

    m_date = re.search(r"(\d{4}-\d{2}-\d{2})|(\d{1,2}[./]\d{1,2})", s)
    if m_date:
        raw = m_date.group(0)
        if "-" in raw:
            deadline = raw
        else:
            dd, mm = raw.split(".") if "." in raw else raw.split("/")
            deadline = f"{now.year}-{mm.zfill(2)}-{dd.zfill(2)}"

    m_dur = re.search(r"(\d+)\s*(мин|m)|(\d+)\s*(ч|h)", s, re.I)
    minutes = 45
    if m_dur:
        if m_dur.group(1):
            minutes = int(m_dur.group(1))
        elif m_dur.group(3):
            minutes = int(m_dur.group(3)) * 60

    m_pr = re.search(r"p([1-3])", s, re.I)
    priority = int(m_pr.group(1)) if m_pr else 2

    title = re.sub(r"p[1-3]", "", s, flags=re.I).strip() or "Без названия"
    return Task(id=uid(), title=title, minutes=minutes, priority=priority, deadline=deadline)


def summarize_text(text: str) -> str:
    import re
    sents = re.split(r"(?<=[.!?])\s+", text.strip())
    if not sents:
        return "Файл пуст или не распознан."
    return "🧠 Ключевые пункты:\n• " + "\n• ".join(sents[:8])


HELP_TEXT = (
    "Привет! Я Focus Planner 🤖\n\n"
    "Команды:\n"
    "• сегодня — показать план на сегодня\n"
    "• добавить <текст> — добавить задачу (естественным языком)\n"
    "• цель <описание> — разбить цель на подзадачи\n"
    "• история — показать все задачи\n"
    "• готово <id> — отметить задачу выполненной\n"
    "• удалить выполненные — очистить завершённые\n"
    "• резюме <текст> — сжать текст до ключевых пунктов\n"
)

pending_subtasks: Dict[int, List[str]] = {}


@dp.bot_started()
async def on_started(event: BotStarted):
    await event.bot.send_message(
        chat_id=event.chat_id,
        text="Привет! Я Focus Planner 🤖\nНапиши 'help', чтобы увидеть команды.",
    )


@dp.message_created()
async def on_message(event: MessageCreated):
    try:
        text = event.message.body.text.strip()
        chat_id = event.message.recipient.chat_id
    except Exception:
        print("⚠️ Ошибка при извлечении данных из события:", event)
        return

    if not text:
        await event.message.answer("Я понимаю только текстовые сообщения 🙂")
        return

    low = text.lower()

    if low in ("help", "/help", "помощь"):
        await event.message.answer(HELP_TEXT)
        return

    if low == "сегодня":
        await event.message.answer(render_plan(greedy_today(store.tasks)))
        return

    if low.startswith("добавить "):
        nl = text.split(" ", 1)[1]
        task = parse_nl(nl)
        store.tasks.append(task)
        save_store(store)
        await event.message.answer(f"✅ Добавил: {task.title} (p{task.priority}, {task.minutes} мин)")
        return

    if low.startswith("цель "):
        goal = text.split(" ", 1)[1]
        subs = smart_subtasks(goal)
        pending_subtasks[chat_id] = subs
        preview = "\n".join([f"{i+1}. {s}" for i, s in enumerate(subs)])
        await event.message.answer(f"🎯 SMART-подзадачи:\n{preview}\n\nДобавь 'в план все' или 'в план 1'")
        return

    if low.startswith("в план"):
        subs = pending_subtasks.get(chat_id, [])
        if not subs:
            await event.message.answer("Нет подзадач. Сначала задай 'цель ...'")
            return
        arg = text.split(" ", 1)[1] if " " in text else ""
        to_add = subs if arg.strip() in ("все", "всё", "all") else [subs[int(arg)-1]] if arg.isdigit() else []
        for s in to_add:
            store.tasks.append(Task(id=uid(), title=s))
        save_store(store)
        await event.message.answer(f"Добавлено {len(to_add)} задач. Напиши 'сегодня', чтобы увидеть.")
        return

    if low.startswith("готово "):
        tid = text.split(" ", 1)[1]
        task = next((t for t in store.tasks if t.id == tid), None)
        if not task:
            await event.message.answer("Не нашёл задачу.")
        else:
            task.done = not task.done
            save_store(store)
            await event.message.answer(f"Статус задачи изменён: {'✔️' if task.done else '↩︎'} {task.title}")
        return

    if low in ("удалить выполненные", "очистить выполненные"):
        store.tasks = [t for t in store.tasks if not t.done]
        save_store(store)
        await event.message.answer("🧹 Удалены все выполненные задачи.")
        return

    if low.startswith("история"):
        q = text[7:].strip()
        await event.message.answer(render_history(store.tasks, q))
        return

    if low.startswith("резюме "):
        content = text.split(" ", 1)[1]
        await event.message.answer(summarize_text(content))
        return

    await event.message.answer("Не понял 😅 Напиши 'help' для списка команд.")


async def main():
    await bot.delete_webhook() 
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
