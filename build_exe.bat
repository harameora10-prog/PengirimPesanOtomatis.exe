@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo   BUILD EXE: Pengirim Pesan Otomatis
echo ============================================
echo.

if not exist "pengirim_pesan_otomatis.py" (
    echo [ERROR] File pengirim_pesan_otomatis.py tidak ditemukan.
    pause
    exit /b 1
)

where python >nul 2>nul
if errorlevel 1 goto nopython

echo [1/2] Memasang pustaka yang diperlukan...
python -m pip install --upgrade pyinstaller pyautogui pyperclip pygetwindow pystray Pillow pytesseract opencv-python
if errorlevel 1 goto gagal

echo.
echo [2/2] Membuat file EXE...
python -m PyInstaller --noconsole --onefile --clean --name PengirimPesanOtomatis pengirim_pesan_otomatis.py
if errorlevel 1 goto gagal

echo.
echo ============================================
echo   SELESAI! EXE berada di folder "dist"
echo ============================================
echo Tesseract OCR harus terpasang terpisah di Windows.
echo Sesuaikan tesseract_path di file Python bila diperlukan.
echo.
pause
exit /b 0

:nopython
echo [ERROR] Python belum terpasang. Unduh dari python.org.
pause
exit /b 1

:gagal
echo [ERROR] Build gagal. Baca pesan error di atas.
pause
exit /b 1
