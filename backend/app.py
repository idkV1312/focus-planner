from __future__ import annotations

import os
import json
import re
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlmodel import SQLModel, Field, Session, create_engine, select
from openai import OpenAI


class Task(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str
    est_minutes: int = 45       
    priority: int = 2           
    done: bool = False
    created: datetime = Field(default_factory=datetime.utcnow)


engine = create_engine("sqlite:///./data.db", echo=False)
SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)



app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],       
    allow_credentials=True,
    allow_methods=["*"],      
    allow_headers=["*"],       
)


HF_TOKEN = os.getenv("HF_TOKEN")


LAST_PLAN: list[dict] = []        
LAST_TASKS_RAW: list[dict] = []  
ORIGINAL_PROMPT: str = ""       


def parse_time(text: str):
    t = text.lower().strip()

    m = re.search(r"(\d{1,2}):(\d{2})", t)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return h, mn

    m = re.search(r"\b(\d{1,2})\s*(утра|вечера)\b", t)
    if m:
        h = int(m.group(1))
        p = m.group(2)

        if p == "вечера" and h < 12:
            h += 12
        if p == "утра" and h == 12:
            h = 0

        return h, 0

    # "в 18"
    m = re.search(r"\bв\s*(\d{1,2})\b", t)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return h, 0

    return None


def parse_duration(text: str):
    t = text.lower()

    # Цифрой
    m = re.search(r"(\d+)\s*мин", t)
    if m:
        return int(m.group(1))

    # Словами
    words = {
        "одну": 1, "одна": 1, "один": 1,
        "две": 2, "два": 2,
        "три": 3,
        "четыре": 4,
        "пять": 5,
        "шесть": 6,
        "семь": 7,
        "восемь": 8,
        "девять": 9,
        "десять": 10
    }

    for w, n in words.items():
        if w in t and "мин" in t:
            return n

    return None


def parse_range(text: str):
    t = text.lower()

    m = re.search(r"(\d{1,2}):(\d{2}).*?(\d{1,2}):(\d{2})", t)
    if m:
        h1, m1, h2, m2 = map(int, m.groups())
        return (h1, m1), (h2, m2)

    m = re.search(r"с\s*(\d{1,2})\s*до\s*(\d{1,2})", t)
    if m:
        h1, h2 = map(int, m.groups())
        return (h1, 0), (h2, 0)

    m = re.search(r"(\d{1,2})\s*[–-]\s*(\d{1,2})", t)
    if m:
        h1, h2 = map(int, m.groups())
        return (h1, 0), (h2, 0)

    return None


def parse_meal_repeats(prompt: str):
    t = prompt.lower()

    m = re.search(r"(\d+)\s*(раз|при[её]мов?)\s*(пищи|еды)?", t)
    if m:
        return int(m.group(1))

    words = {
        "два": 2, "две": 2,
        "три": 3,
        "четыре": 4,
        "пять": 5,
        "шесть": 6,
        "семь": 7
    }

    for w, n in words.items():
        if w in t and ("пищ" in t or "еды" in t):
            return n

    return None


def parse_wake_sleep(prompt: str):
    text = prompt.lower()

    if "послезавтра" in text:
        day_label = "послезавтра"
    elif "завтра" in text:
        day_label = "завтра"
    else:
        day_label = "сегодня"

    start_h, start_m = 8, 0
    end_h, end_m = 22, 0

    mw = re.search(r"(встаю|просыпаюсь|подъ[её]м)[^.,;]*", text)
    if mw:
        t = parse_time(mw.group(0))
        if t:
            start_h, start_m = t

    ms = re.search(r"(ложусь|спать|иду спать)[^.,;]*", text)
    if ms:
        t = parse_time(ms.group(0))
        if t:
            end_h, end_m = t

    return start_h, start_m, end_h, end_m, day_label




def normalize_text(t: str) -> str:
    t = t.lower().strip()
    t = re.sub(r"[^а-яa-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t)


def semantic_match_task(prompt: str, tasks: list[dict]) -> int | None:
    if not tasks:
        return None

    np = normalize_text(prompt)

    best_score = 0
    best_idx = None

    for i, t in enumerate(tasks):
        tt = normalize_text(t["title"])
        score = 0

        if tt in np:
            score += 5

        for w in tt.split():
            if len(w) > 3 and w in np:
                score += 3

        for w1 in tt.split():
            for w2 in np.split():
                if w1.startswith(w2) or w2.startswith(w1):
                    score += 1

        if score > best_score:
            best_score = score
            best_idx = i

    return best_idx if best_score > 0 else None




def rebuild_plan_from_tasks(tasks: list[dict], prompt: str):
    schedule, day_label = plan_day(prompt, tasks)
    return schedule, day_label


def plan_day(prompt: str, base_tasks: list[dict]):


    start_h, start_m, end_h, end_m, day_label = parse_wake_sleep(prompt)

    day_start = datetime.now().replace(
        hour=start_h, minute=start_m, second=0, microsecond=0
    )
    day_end = datetime.now().replace(
        hour=end_h, minute=end_m, second=0, microsecond=0
    )

    if day_end <= day_start:
        day_end += timedelta(hours=16)

    total_day_minutes = int((day_end - day_start).total_seconds() // 60)

    meal_count = parse_meal_repeats(prompt) or 0
    meal_duration = None
    normal_tasks = []

    for t in base_tasks:
        title = t["title"]
        est = max(5, int(t["est_minutes"]))
        pr = t["priority"]

        low = title.lower()

        is_meal = any(
            w in low for w in
            ["прием пищи", "приём пищи", "еда", "поесть", "перекус"]
        )

        if is_meal and meal_count > 0:
            if meal_duration is None:
                meal_duration = est if est <= 60 else 20
        else:
            normal_tasks.append({
                "title": title,
                "est_minutes": est,
                "priority": pr
            })

    if meal_count > 0 and meal_duration is None:
        meal_duration = 20

    normal_tasks.sort(key=lambda x: x["priority"])

    meal_slots = []

    if meal_count > 0:
        interval = total_day_minutes / (meal_count + 1)

        for i in range(meal_count):
            center_min = int(interval * (i + 1))

            start_min = max(0, center_min - meal_duration // 2)
            if start_min + meal_duration > total_day_minutes:
                start_min = total_day_minutes - meal_duration

            ms = day_start + timedelta(minutes=start_min)
            me = ms + timedelta(minutes=meal_duration)

            meal_slots.append((ms, me))

    segments = []
    cur = day_start

    for ms, me in meal_slots:
        if ms > cur:
            segments.append({
                "start": cur,
                "end": ms,
                "cursor": cur
            })
        cur = me

    if cur < day_end:
        segments.append({
            "start": cur,
            "end": day_end,
            "cursor": cur
        })

    schedule = []
    tail_cursor = day_end 

    for task in normal_tasks:
        est = task["est_minutes"]
        placed = False

        for seg in segments:
            free = int((seg["end"] - seg["cursor"]).total_seconds() // 60)

            if free >= est:
                st = seg["cursor"]
                en = st + timedelta(minutes=est)

                seg["cursor"] = en

                schedule.append({
                    "title": task["title"],
                    "est_minutes": est,
                    "priority": task["priority"],
                    "manual_start": st.strftime("%H:%M")
                })

                placed = True
                break

        if not placed:
            st = tail_cursor
            en = st + timedelta(minutes=est)
            tail_cursor = en

            schedule.append({
                "title": task["title"],
                "est_minutes": est,
                "priority": task["priority"],
                "manual_start": st.strftime("%H:%M")
            })

    for i, (ms, me) in enumerate(meal_slots, start=1):
        schedule.append({
            "title": f"Приём пищи №{i}",
            "est_minutes": int((me - ms).total_seconds() // 60),
            "priority": 2,
            "manual_start": ms.strftime("%H:%M")
        })

    schedule.sort(key=lambda x: x["manual_start"])

    return schedule, day_label


def build_schedule_text(tasks: list[dict], day_label: str):
    if not tasks:
        return "Не удалось составить расписание."

    lines: list[str] = [f"Ваше расписание на {day_label}:"]

    for t in tasks:
        st = t.get("manual_start") or "08:00"
        h, m = map(int, st.split(":"))
        start_dt = datetime.now().replace(
            hour=h, minute=m, second=0, microsecond=0
        )
        end_dt = start_dt + timedelta(minutes=int(t.get("est_minutes", 30)))

        lines.append(
            f"{start_dt.strftime('%H:%M')}–{end_dt.strftime('%H:%M')} — {t['title']}"
        )

    return "\n".join(lines)



@app.get("/tasks")
def get_tasks():
    with get_session() as session:
        tasks = session.exec(select(Task).order_by(Task.priority, Task.id)).all()
        return tasks


@app.post("/add_task")
def add_task(title: str):
    with get_session() as session:
        t = Task(title=title)
        session.add(t)
        session.commit()
    return {"ok": True}


@app.post("/done")
def mark_done(task_id: int):
    with get_session() as session:
        t = session.get(Task, task_id)
        if t:
            t.done = True
            session.add(t)
            session.commit()
    return {"ok": True}


@app.post("/ask")
async def ask_ai(request: Request):

    global LAST_PLAN, LAST_TASKS_RAW, ORIGINAL_PROMPT

    data = await request.json()
    prompt = (data.get("prompt") or "").strip()
    pl = prompt.lower()

    if not prompt:
        return {"ok": False, "answer": "Пустой запрос."}


    if any(k in pl for k in ["новые задачи", "заново", "с нуля", "начать заново"]):
        LAST_PLAN = []
        LAST_TASKS_RAW = []
        ORIGINAL_PROMPT = ""
        return {"ok": True, "answer": "Ок! Напишите задачи, и я составлю новое расписание."}

    if pl.strip() == "оставь":
        if not LAST_PLAN:
            return {"ok": True, "answer": "Сейчас нет расписания, которое можно сохранить."}

        with get_session() as session:
            for t in LAST_PLAN:
                session.add(Task(
                    title=t["title"],
                    est_minutes=t["est_minutes"],
                    priority=t["priority"]
                ))
            session.commit()

        return {"ok": True, "answer": "Готово! Расписание сохранено ✔️"}


    if LAST_PLAN:
        change_words = [
            "измени", "переделай", "поменяй", "сдвинь", "подвинь",
            "поправь", "короче", "дольше", "позже", "раньше",
            "время", "диапазон", "минут", "час"
        ]

        goal_words = [
            "хочу", "надо", "нужно", "планирую", "собираюсь"
        ]

        is_change_request = any(w in pl for w in change_words)
        is_new_goals = any(w in pl for w in goal_words)

        if is_new_goals and not is_change_request:
            LAST_PLAN = []
            LAST_TASKS_RAW = []
            ORIGINAL_PROMPT = ""

        else:
            new_range = parse_range(prompt)
            if new_range:
                idx = semantic_match_task(prompt, LAST_PLAN)
                if idx is not None:
                    (h1, m1), (h2, m2) = new_range
                    st = datetime.now().replace(hour=h1, minute=m1)
                    en = datetime.now().replace(hour=h2, minute=m2)
                    if en <= st:
                        en += timedelta(hours=12)
                    dur = int((en - st).total_seconds() // 60)

                    LAST_PLAN[idx]["manual_start"] = f"{h1:02d}:{m1:02d}"
                    LAST_PLAN[idx]["est_minutes"] = dur

                    LAST_PLAN, day_label = rebuild_plan_from_tasks(LAST_PLAN, ORIGINAL_PROMPT)
                    txt = build_schedule_text(LAST_PLAN, day_label)
                    return {"ok": True, "answer": txt + "\n\nДиапазон обновлён. Напишите «оставь», чтобы сохранить."}

            new_time = parse_time(prompt)
            if new_time:
                idx = semantic_match_task(prompt, LAST_PLAN)
                if idx is not None:
                    h, m = new_time
                    LAST_PLAN[idx]["manual_start"] = f"{h:02d}:{m:02d}"

                    LAST_PLAN, day_label = rebuild_plan_from_tasks(LAST_PLAN, ORIGINAL_PROMPT)
                    txt = build_schedule_text(LAST_PLAN, day_label)
                    return {"ok": True, "answer": txt + "\n\nВремя обновлено. Напишите «оставь», чтобы сохранить."}

            new_dur = parse_duration(prompt)
            if new_dur:
                idx = semantic_match_task(prompt, LAST_PLAN)
                if idx is not None:
                    LAST_PLAN[idx]["est_minutes"] = new_dur

                    LAST_PLAN, day_label = rebuild_plan_from_tasks(LAST_PLAN, ORIGINAL_PROMPT)
                    txt = build_schedule_text(LAST_PLAN, day_label)
                    return {"ok": True, "answer": txt + "\n\nДлительность обновлена. Напишите «оставь», чтобы сохранить."}

            new_meals = parse_meal_repeats(prompt)
            if new_meals:
                new_tasks = [
                    t for t in LAST_TASKS_RAW
                    if "пищ" not in t["title"].lower() and "еда" not in t["title"].lower()
                ]
                new_tasks.append({
                    "title": "Приём пищи",
                    "est_minutes": 20,
                    "priority": 2
                })

                ORIGINAL_PROMPT += f". Нужно {new_meals} приёмов пищи"
                LAST_TASKS_RAW = new_tasks

                LAST_PLAN, day_label = plan_day(ORIGINAL_PROMPT, LAST_TASKS_RAW)
                txt = build_schedule_text(LAST_PLAN, day_label)
                return {"ok": True, "answer": txt + "\n\nПриёмы пищи обновлены. Напишите «оставь», чтобы сохранить."}

            return {
                "ok": True,
                "answer": "Я не понял, что нужно изменить. Укажите конкретное время, длительность или диапазон (например, «в 22:00», «за 5 минут», «с 14:00 до 16:00») или напишите «новые задачи»."
            }


    ORIGINAL_PROMPT = prompt

    system_prompt = (
        "Ты ассистент-планировщик. Верни строго JSON:\n"
        "{\"tasks\":[{\"title\":\"...\",\"est_minutes\":30,\"priority\":2}]}\n"
        "title — короткое действие.\n"
        "est_minutes — длительность в минутах.\n"
        "priority — 1 (важно), 2 (нормально), 3 (неважно).\n"
        "Не назначай время, только выделяй задачи."
    )

    client = OpenAI(
        base_url="https://router.huggingface.co/v1",
        api_key=HF_TOKEN
    )

    resp = client.chat.completions.create(
        model="CohereLabs/c4ai-command-r7b-12-2024:cohere",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt}
        ]
    )

    raw = resp.choices[0].message.content or ""
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"ok": True, "answer": "Не удалось извлечь задачи из текста."}

    parsed = json.loads(m.group(0))
    tasks_raw = parsed.get("tasks", [])

    LAST_TASKS_RAW = []
    for t in tasks_raw:
        title = (t.get("title") or "").strip()
        if not title:
            continue

        est = max(5, int(t.get("est_minutes") or 30))
        pr = int(t.get("priority") or 2)
        if pr not in (1, 2, 3):
            pr = 2

        LAST_TASKS_RAW.append({
            "title": title,
            "est_minutes": est,
            "priority": pr
        })

    if not LAST_TASKS_RAW:
        return {"ok": True, "answer": "Я не нашёл задач в вашем сообщении."}

    LAST_PLAN, day_label = plan_day(prompt, LAST_TASKS_RAW)
    txt = build_schedule_text(LAST_PLAN, day_label)

    return {
        "ok": True,
        "answer": txt + "\n\nЕсли хотите что-то уточнить — просто напишите. Чтобы сохранить — «оставь»."
    }