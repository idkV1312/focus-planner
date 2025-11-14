const API = "http://127.0.0.1:8000";


async function loadTasks() {
  const box = document.getElementById("todayList");
  if (!box) return;

  box.innerHTML = `<div class="empty-card">⏳ Загружаю...</div>`;

  try {
    const res = await fetch(`${API}/tasks`);
    const tasks = await res.json();

    if (!Array.isArray(tasks) || tasks.length === 0) {
      box.innerHTML = `<div class="empty-card">Задач пока нет 🎉</div>`;
      return;
    }

    const plan = tasks
      .filter(t => !t.done)
      .sort((a, b) => a.priority - b.priority);

    let cur = new Date();
    cur.setSeconds(0, 0);
    box.innerHTML = "";

    for (const t of plan) {
      const est = t.est_minutes || 45;
      const start = new Date(cur);
      const end = new Date(cur.getTime() + est * 60000);

      const el = document.createElement("div");
      el.className = "task-card";
      el.innerHTML = `
        <div class="left">
          <div class="title">${t.title}</div>
          <div class="meta">
            ${start.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}
            –
            ${end.toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}
            · p${t.priority} · ${est} мин
          </div>
        </div>
        <div class="right">
          <button class="chip success" data-id="${t.id}">Готово</button>
        </div>`;
      
      box.appendChild(el);
      cur = end;
    }

    box.querySelectorAll(".chip.success").forEach(b => {
      b.addEventListener("click", async () => {
        const id = b.dataset.id;
        await fetch(`${API}/done?task_id=${id}`, { method: "POST" });
        loadTasks();
      });
    });

  } catch (err) {
    console.error("Ошибка загрузки задач:", err);
    box.innerHTML = `<div class="empty-card">⚠️ Ошибка загрузки задач</div>`;
  }
}



async function addTask(title) {
  try {
    await fetch(`${API}/add_task?title=${encodeURIComponent(title)}`, {
      method: "POST"
    });
    loadTasks();
  } catch (err) {
    console.error("Ошибка добавления задачи:", err);
  }
}


let aiBusy = false;

async function askAI() {
  if (aiBusy) {
    console.warn("⏳ Уже идёт запрос к AI, подожди...");
    return;
  }

  const input = document.getElementById("aiPrompt");
  const history = document.getElementById("aiChatHistory");
  const typing = document.getElementById("aiTyping");
  const btn = document.getElementById("btnAskAI");

  if (!input || !history || !typing || !btn) return;

  const userText = input.value.trim();
  if (!userText) return;

  aiBusy = true;

  const userMsg = document.createElement("div");
  userMsg.className = "message user";
  userMsg.innerHTML = `<div class="bubble">${userText}</div>`;
  history.appendChild(userMsg);
  history.scrollTop = history.scrollHeight;

  input.value = "";

  typing.classList.remove("hidden");
  btn.disabled = true;

  try {
    const r = await fetch(`${API}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: userText })
    });

    const data = await r.json();
    console.log("Ответ от /ask:", data);

    let answerText = "⚠️ Не удалось получить ответ от ассистента.";

    if (data.ok && data.answer) {
      answerText = data.answer;

     
      if (answerText.includes("Расписание составлено")) {
        console.log("🗓️ Обновляем расписание (вкладка Сегодня)");
        loadTasks();

        document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
        const todayTabBtn = document.querySelector('[data-tab="today"]');
        if (todayTabBtn) todayTabBtn.classList.add("active");

        document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
        const todayPanel = document.getElementById("tab-today");
        if (todayPanel) todayPanel.classList.add("active");
      }
    }

    const botMsg = document.createElement("div");
    botMsg.className = "message bot";
    botMsg.innerHTML = `<div class="bubble">${answerText.replace(/\n/g, "<br>")}</div>`;
    history.appendChild(botMsg);
    history.scrollTop = history.scrollHeight;

  } catch (err) {
    console.error("🔴 Ошибка при запросе к AI:", err);
    const botMsg = document.createElement("div");
    botMsg.className = "message bot";
    botMsg.innerHTML = `<div class="bubble">Ошибка связи с сервером AI 🤖</div>`;
    history.appendChild(botMsg);
    history.scrollTop = history.scrollHeight;
  } finally {
    aiBusy = false;
    typing.classList.add("hidden");
    btn.disabled = false;
  }
}


const voiceBtn = document.getElementById("btnVoice");
let recognition;

if (voiceBtn && ("webkitSpeechRecognition" in window || "SpeechRecognition" in window)) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  recognition = new SpeechRecognition();
  recognition.lang = "ru-RU";
  recognition.interimResults = false;
  recognition.maxAlternatives = 1;

  recognition.onstart = () => {
    voiceBtn.textContent = "🎙️"; 
    voiceBtn.disabled = true;
  };

  recognition.onerror = (e) => {
    console.error("Ошибка распознавания:", e.error);
    voiceBtn.textContent = "🎤";
    voiceBtn.disabled = false;
  };

  recognition.onend = () => {
    voiceBtn.textContent = "🎤";
    voiceBtn.disabled = false;
  };

  recognition.onresult = (e) => {
    const transcript = e.results[0][0].transcript;
    console.log("🗣️ Распознано:", transcript);
    const input = document.getElementById("aiPrompt");
    if (input) {
      input.value = transcript;
      askAI(); 
    }
  };

  voiceBtn.addEventListener("click", () => recognition.start());
  } else if (voiceBtn) {
    voiceBtn.disabled = true;
    voiceBtn.textContent = "🚫";
    voiceBtn.title = "Голос недоступен в этом браузере";
    console.warn("SpeechRecognition не поддерживается в этом браузере");
  }


  document.addEventListener("DOMContentLoaded", () => {
    const askBtn = document.getElementById("btnAskAI");
    if (askBtn) askBtn.addEventListener("click", askAI);

    const aiInput = document.getElementById("aiPrompt");
    if (aiInput) {
      aiInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          askAI();
        }
      });
    }

    const rebuildBtn = document.getElementById("btnRebuild");
    if (rebuildBtn) rebuildBtn.addEventListener("click", loadTasks);



    loadTasks();

    


});