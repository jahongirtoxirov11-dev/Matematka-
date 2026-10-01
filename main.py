import os
import asyncio
import logging
import math
import pickle
from datetime import datetime, timedelta
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# .env faylidan maxfiy ma'lumotlarni yuklash
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()
scheduler = AsyncIOScheduler()

# --- HOLATLAR (FSM) ---
class UserState(StatesGroup):
    register = State()

class AdminState(StatesGroup):
    waiting_for_pdf = State()
    waiting_for_answers = State()
    waiting_for_duration = State()
    waiting_for_levels = State()
    waiting_for_broadcast = State()

# --- BAZA SIMULYATSIYASI ---
users_db = {}  
tests_db = {}  
test_counter = 1  
user_current_test = {} 

# --- MA'LUMOTLARNI SAQLASH VA TIKLASH FUNKSIYALARI ---
def save_data():
    try:
        with open("database.pkl", "wb") as f:
            pickle.dump({
                "users_db": users_db,
                "tests_db": tests_db,
                "test_counter": test_counter,
                "user_current_test": user_current_test
            }, f)
    except Exception as e:
        logging.error(f"Saqlashda xatolik: {e}")

def load_data():
    global test_counter
    if os.path.exists("database.pkl"):
        try:
            with open("database.pkl", "rb") as f:
                data = pickle.load(f)
                users_db.update(data.get("users_db", {}))
                tests_db.update(data.get("tests_db", {}))
                test_counter = data.get("test_counter", 1)
                user_current_test.update(data.get("user_current_test", {}))
        except Exception as e:
            logging.error(f"Yuklashda xatolik: {e}")

# --- KLAVIATURALAR ---
def admin_panel_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Yangi mock yaratish", callback_data="new_test")],
        [InlineKeyboardButton(text="📢 E'lon yuborish", callback_data="send_broadcast")],
        [InlineKeyboardButton(text="❌ Faol mockni to'xtatish", callback_data="stop_active_mock")]
    ])

def options_keyboard(is_admin=False):
    keys = [
        [KeyboardButton(text="A"), KeyboardButton(text="B")],
        [KeyboardButton(text="C"), KeyboardButton(text="D")]
    ]
    if is_admin:
        keys.append([KeyboardButton(text="✅ Tayyor")]) 
    
    return ReplyKeyboardMarkup(keyboard=keys, resize_keyboard=True, is_persistent=True)

# --- FOYDALANUVCHI QISMI ---

@router.message(CommandStart())
async def start_cmd(message: Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in users_db:
        await message.answer("Assalomu alaykum! Ism va familiyangizni kiriting (Masalan: Aliyev Vali):", reply_markup=ReplyKeyboardRemove())
        await state.set_state(UserState.register)
    else:
        await show_active_mocks(message)

@router.message(UserState.register)
async def register_user(message: Message, state: FSMContext):
    users_db[message.from_user.id] = message.text
    await message.answer("Ro'yxatdan muvaffaqiyatli o'tdingiz!")
    await show_active_mocks(message)
    await state.clear()

async def show_active_mocks(message: Message):
    active_mocks = {tid: t for tid, t in tests_db.items() if t["is_active"]}
    if not active_mocks:
        await message.answer("Hozircha faol mock testlar mavjud emas.", reply_markup=ReplyKeyboardRemove())
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for tid in active_mocks.keys():
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"📚 Mock Test #{tid} ni ishlash", callback_data=f"join_{tid}")])
        
    await message.answer("👇 Quyidagi faol mocklardan birini tanlang:", reply_markup=kb)

@router.callback_query(F.data.startswith("join_"))
async def join_test(call: CallbackQuery):
    test_id = int(call.data.split("_")[1])
    
    if test_id not in tests_db or not tests_db[test_id]["is_active"]:
        await call.answer("Bu test vaqti tugagan yoki mavjud emas!", show_alert=True)
        return
        
    user_id = call.from_user.id
    
    if user_id in tests_db[test_id]["results"]:
        await call.answer("Siz oldin bu testga javob bergansiz", show_alert=True)
        await bot.send_message(user_id, "Siz oldin bu testga javob bergansiz.")
        return
        
    user_current_test[user_id] = test_id
    
    caption = "Mock test boshlandi ishlashingiz mumkin"
    await bot.send_document(
        chat_id=user_id, 
        document=tests_db[test_id]["pdf_id"], 
        caption=caption,
        reply_markup=options_keyboard(is_admin=False)
    )
    await call.answer()

@router.message(F.text.in_(['A', 'B', 'C', 'D']), lambda msg: msg.from_user.id in user_current_test and msg.from_user.id != ADMIN_ID)
async def receive_user_answer(message: Message):
    user_id = message.from_user.id
    test_id = user_current_test[user_id]
    test_data = tests_db[test_id]
    
    if not test_data["is_active"]:
        await message.answer("Kechirasiz, bu test vaqti tugagan.", reply_markup=ReplyKeyboardRemove())
        user_current_test.pop(user_id, None)
        return
        
    total_q = len(test_data["answers"])
    ans = message.text.lower()
    
    if user_id not in test_data["user_progress"]:
        test_data["user_progress"][user_id] = []
        
    progress = test_data["user_progress"][user_id]
    
    if len(progress) < total_q:
        progress.append(ans)
        current_q = len(progress)
        
        if current_q == total_q:
            test_data["results"][user_id] = "".join(progress)
            await message.answer("✅ Barcha savollarga javob berdingiz! Test vaqti tugashini kuting.", reply_markup=ReplyKeyboardRemove())
            user_current_test.pop(user_id, None)
        else:
            await message.answer(f"✅ {current_q}-savol qabul qilindi. {current_q + 1}-savol javobini belgilang:")
    else:
        await message.answer("Siz bu testdagi barcha savollarga javob bergansiz.", reply_markup=ReplyKeyboardRemove())


# --- ADMIN QISMI ---

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if message.from_user.id == ADMIN_ID:
        await message.answer("Admin paneliga xush kelibsiz. Nima qilamiz?", reply_markup=admin_panel_keyboard())

@router.callback_query(F.data == "stop_active_mock")
async def stop_mock_list(call: CallbackQuery):
    active_mocks = {tid: t for tid, t in tests_db.items() if t["is_active"]}
    if not active_mocks:
        await call.answer("Hozircha faol mocklar yo'q.", show_alert=True)
        return
    
    kb = InlineKeyboardMarkup(inline_keyboard=[])
    for tid in active_mocks.keys():
        kb.inline_keyboard.append([InlineKeyboardButton(text=f"❌ Mock #{tid} ni to'xtatish", callback_data=f"force_stop_{tid}")])
        
    await call.message.answer("Qaysi mockni muddatidan oldin to'xtatib natijalarni hisoblatmoqchisiz?", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("force_stop_"))
async def force_stop_mock(call: CallbackQuery):
    test_id = int(call.data.split("_")[2])
    
    if test_id in tests_db and tests_db[test_id]["is_active"]:
        await call.message.edit_text(f"⏳ Mock #{test_id} muddatidan oldin to'xtatilmoqda...")
        await finish_test(test_id)
    else:
        await call.answer("Bu test allaqachon to'xtatilgan yoki mavjud emas.", show_alert=True)

@router.callback_query(F.data == "new_test")
async def new_test_start(call: CallbackQuery, state: FSMContext):
    await call.message.answer("Yangi testning PDF variantini yuboring:")
    await state.set_state(AdminState.waiting_for_pdf)

@router.message(AdminState.waiting_for_pdf, F.document)
async def get_pdf(message: Message, state: FSMContext):
    await state.update_data(pdf_id=message.document.file_id, admin_answers="")
    await message.answer("Qabul qilindi. Endi to'g'ri javoblarni paski tugmalar (A, B, C, D) orqali bittalab belgilang. Tugatgach '✅ Tayyor' tugmasini bosing:", 
                         reply_markup=options_keyboard(is_admin=True))
    await state.set_state(AdminState.waiting_for_answers)

@router.message(AdminState.waiting_for_answers, F.text.in_(['A', 'B', 'C', 'D', '✅ Tayyor']))
async def get_admin_answers(message: Message, state: FSMContext):
    text = message.text
    data = await state.get_data()
    current_answers = data.get("admin_answers", "")
    
    if text in ['A', 'B', 'C', 'D']:
        current_answers += text.lower()
        await state.update_data(admin_answers=current_answers)
        await message.answer(f"Qabul qilindi: {len(current_answers)}-savol. (Joriy kalitlar: {current_answers.upper()})")
        
    elif text == "✅ Tayyor":
        if len(current_answers) == 0:
            await message.answer("Hech qanday javob kiritmadingiz!")
            return
            
        await state.update_data(answers=current_answers)
        await message.answer(f"Jami {len(current_answers)} ta savol kaliti saqlandi.\n\nTest qancha vaqt davom etadi? (Minutlarda yozing, masalan: 60)", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminState.waiting_for_duration)

@router.message(AdminState.waiting_for_duration)
async def get_duration(message: Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Faqat raqam kiriting (minutlarda):")
        return
    await state.update_data(duration=int(message.text))
    
    text = ("Vaqt qabul qilindi. Endi darajalarni kiriting.\n"
            "Format: Min_ball-Max_ball:Daraja nomi (Alohida qatorda yozing)\n"
            "Masalan:\n0-45.99:Daraja ololmadingiz\n46-49.9:C\n50-54.9:C+\n55-59.9:B\n60-64.9:B+\n65-69.9:A\n70-100:A+")
    await message.answer(text)
    await state.set_state(AdminState.waiting_for_levels)

@router.message(AdminState.waiting_for_levels)
async def get_levels(message: Message, state: FSMContext):
    levels = []
    try:
        lines = message.text.split('\n')
        for line in lines:
            range_part, name = line.split(':')
            min_score, max_score = range_part.split('-')
            levels.append({"min": float(min_score), "max": float(max_score), "name": name.strip()})
    except Exception:
        await message.answer("Format xato. Qaytadan kiriting:")
        return

    data = await state.get_data()
    global test_counter
    test_id = test_counter
    duration = data['duration']
    
    run_time = datetime.now() + timedelta(minutes=duration)
    
    tests_db[test_id] = {
        "is_active": True,
        "pdf_id": data['pdf_id'],
        "answers": data['answers'],
        "levels": levels,
        "results": {},
        "user_progress": {},
        "end_time": run_time.timestamp() # Tiklash uchun tugash vaqtini saqlaymiz
    }
    test_counter += 1
    
    await broadcast_new_test_alert(test_id, duration)
    
    scheduler.add_job(finish_test, 'date', run_date=run_time, args=[test_id])
    
    await message.answer(f"✅ Mock Test #{test_id} ishga tushdi va hamma o'quvchilarga xabar berildi.")
    await state.clear()

# --- E'LON YUBORISH ---

@router.callback_query(F.data == "send_broadcast")
async def ask_broadcast(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await call.message.answer("📣 E'lon matnini, rasmini yoki videoni yuboring:")
    await state.set_state(AdminState.waiting_for_broadcast)

@router.message(AdminState.waiting_for_broadcast)
async def send_broadcast_msg(message: Message, state: FSMContext):
    await state.clear()
    
    if not users_db:
        await message.answer("⚠️ Hozircha bazada hech qanday foydalanuvchi yo'q. E'lon yuborilmadi.")
        return

    await message.answer("⏳ E'lon tarqatish boshlandi, kuting...")
    success = 0
    
    for user_id in users_db.keys():
        try:
            await message.copy_to(chat_id=user_id)
            success += 1
            await asyncio.sleep(0.05) 
        except Exception:
            pass
            
    await message.answer(f"✅ E'lon {len(users_db)} ta foydalanuvchidan {success} tasiga muvaffaqiyatli yuborildi.")


# --- XABAR TARQATISH VA RASCH MODELI ---

async def broadcast_new_test_alert(test_id: int, duration: int):
    caption = f"📣 Yangi Mock Test (#{test_id}) bazaga qo'shildi!\n⏳ Ajratilgan vaqt: {duration} minut.\n\nQatnashish uchun /start ni bosing."
    for user_id in users_db.keys():
        try:
            await bot.send_message(chat_id=user_id, text=caption)
            await asyncio.sleep(0.05)
        except Exception:
            pass

async def finish_test(test_id: int):
    test_data = tests_db.get(test_id)
    if not test_data or not test_data["is_active"]: return
    
    test_data["is_active"] = False 
    await bot.send_message(ADMIN_ID, f"Mock Test #{test_id} vaqti tugadi. Natijalar Rasch modelida hisoblanmoqda...")
    
    results_text, final_scores = calculate_rasch_model(test_id)
    
    if not final_scores:
        await bot.send_message(ADMIN_ID, "Bu testda hech kim to'liq qatnashmadi.")
        return

    filename = f"natijalar_test_{test_id}.txt"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(results_text)
    
    # YANGILIK: Matn ko'rinishida to'g'ridan-to'g'ri kanal va adminga yuborish
    try:
        if len(results_text) < 4000:
            await bot.send_message(ADMIN_ID, f"📊 Natijalar:\n\n{results_text}")
            await bot.send_message("@Toxirov_Office_Matematika", f"📊 Mock Test #{test_id} natijalari:\n\n{results_text}")
            
        await bot.send_document(ADMIN_ID, FSInputFile(filename), caption=f"Test #{test_id} fayl shaklidagi natijalari:")
        await bot.send_document("@Toxirov_Office_Matematika", FSInputFile(filename), caption=f"📊 Mock Test #{test_id} fayl shaklidagi natijalari")
    except Exception as e:
        await bot.send_message(ADMIN_ID, f"⚠️ Natijalarni kanalga yuborishda xatolik yuz berdi. Bot kanalda admin ekanligini tekshiring! Xato: {e}")
    
    for user_id, data in final_scores.items():
        try:
            foydalanuvchi_ismi = users_db.get(user_id, "Noma'lum")
            shaxsiy_matn = (f"🎯 Mock Test #{test_id} yakunlandi!\n\n"
                            f"👤 Ism: {foydalanuvchi_ismi}\n"
                            f"📈 Sizning balingiz: {data['score']:.1f} / 100\n"
                            f"🏆 Darajangiz: {data['level']}\n\n"
                            f"Umumiy ro'yxat kanalda: @Toxirov_Office_Matematika")
            
            await bot.send_message(user_id, shaxsiy_matn, reply_markup=ReplyKeyboardRemove())
            await asyncio.sleep(0.05)
        except Exception:
            pass

def calculate_rasch_model(test_id: int):
    test_data = tests_db[test_id]
    correct_answers = test_data["answers"]
    user_results = test_data["results"]
    total_users = len(user_results)
    
    if total_users == 0: 
        return "Hech kim to'liq qatnashmadi.", {}

    question_counts = len(correct_answers)
    q_correct_counts = {i: 0 for i in range(question_counts)}
    
    for uid, u_ans in user_results.items():
        for i, ans in enumerate(u_ans):
            if i < question_counts and ans == correct_answers[i]:
                q_correct_counts[i] += 1

    q_weights = {}
    for i in range(question_counts):
        c = q_correct_counts[i]
        c = max(0.5, min(c, total_users - 0.5)) 
        difficulty = math.log((total_users - c) / c)
        q_weights[i] = difficulty

    min_weight = min(q_weights.values()) if q_weights else 0
    for i in q_weights:
        q_weights[i] = q_weights[i] - min_weight + 1 

    total_weight = sum(q_weights.values())
    
    final_scores = {}
    for uid, u_ans in user_results.items():
        score = 0
        for i, ans in enumerate(u_ans):
            if i < question_counts and ans == correct_answers[i]:
                score += q_weights[i]
        
        percent_score = (score / total_weight) * 100 if total_weight > 0 else 0
        level_name = "Daraja aniqlanmadi"
        for level in test_data["levels"]:
            if level["min"] <= percent_score <= level["max"]:
                level_name = level["name"]
                break
                
        final_scores[uid] = {"score": percent_score, "level": level_name}

    sorted_users = sorted(final_scores.items(), key=lambda item: item[1]['score'], reverse=True)
    
    # YANGILIK: \n o'rniga \r\n (Bloknot uchun qator tashlash qoidasi)
    report = f"Mock Test #{test_id} Natijalari\r\nID | Ism Familiya | Ball | Daraja\r\n" + ("-" * 40) + "\r\n"
    
    for uid, data in sorted_users:
        name = users_db.get(uid, "Noma'lum")
        report += f"{uid} | {name} | {data['score']:.1f} | {data['level']}\r\n"
        
    return report, final_scores


# --- RENDER UCHUN VEB-SERVER VA ISHGA TUSHIRISH ---

async def handle_ping(request):
    return web.Response(text="Bot is running in Live mode with Database Protection!")

async def main():
    # 1. BAZANI YUKLASH (Server o'chgan bo'lsa tiklash)
    load_data()
    
    scheduler.start()
    dp.include_router(router)
    
    # 2. SERVER O'CHGANDA YO'QOLGAN TAYMERLARNI TIKLASH
    for tid, t_data in tests_db.items():
        if t_data.get("is_active"):
            end_time = datetime.fromtimestamp(t_data["end_time"])
            if end_time > datetime.now():
                scheduler.add_job(finish_test, 'date', run_date=end_time, args=[tid])
            else:
                # Agar server o'chgan paytda vaqt tugab qolgan bo'lsa, darhol yakunlash
                asyncio.create_task(finish_test(tid))
                
    # 3. HAR 10 SONIYADA BAZANI SAQLASH (Xavfsizlik)
    scheduler.add_job(save_data, 'interval', seconds=10)
    
    # Render uchun fake veb-serverni ishga tushirish
    app = web.Application()
    app.router.add_get('/', handle_ping)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Render avtomatik beradigan portni ushlab olish
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    # Botni ishga tushirish (Polling)
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
