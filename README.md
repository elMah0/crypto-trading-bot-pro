# CryptoTradingBot Pro 🚀 — Bot de Trading con Interfaz Gráfica (GUI Web)

Bot de trading algorítmico multitemporal en Python con **Interfaz Gráfica de Usuario (GUI Web en tiempo real)**, análisis técnico (10D SMA/ADX + 1H RSI/Volumen), gestión avanzada de riesgo (Trailing Stop Loss dinámico, cortocircuitos de seguridad), simulación (**Dry-Run**) y alertas en **Telegram**.

---

## 🖥️ Interfaz Gráfica de Usuario (Dashboard Web)

El bot incluye una **GUI Web moderna en modo oscuro financiero (*Dark FinTech*)** accesible desde cualquier navegador (`http://localhost:8000`), la cual provee:

1. **Centro de Control Algorítmico**:
   - Monitoreo en vivo de Balance Total y Disponible.
   - PnL acumulado diario en 24h, Win Rate % y conteo de posiciones.
   - Botón de **Pausar / Reanudar** el bot en caliente.
   - Botón de **Escanear Ahora** para forzar un análisis técnico manual inmediato.
2. **Gráficos Profesionales de TradingView (Lightweight Charts)**:
   - Velas japonesas interactivas en tiempo real.
   - Indicador de Media Móvil de 10 días (SMA 10) y panel de volumen.
   - Selector de pares (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`) y alternancia rápida de marcos temporales (`1H` Micro / `1D` Macro).
3. **Gestión Visual de Posiciones Abiertas**:
   - Detalle de precio de entrada, precio actual, Stop Loss y Take Profit.
   - Indicador de estado de **Trailing Stop Loss dinámico**.
   - Botón interactivo para **cerrar manualmente cualquier posición** desde el navegador.
4. **Consola de Operaciones en Vivo (WebSockets)**:
   - Transmisión continua de logs, señales y eventos sin recargar la página.
5. **Diagnóstico Técnico Multitemporal**:
   - Pestaña con desglose de indicadores (SMA10, ADX14, RSI14, Volumen) y justificación de entrada/espera.
6. **Historial de Operaciones y Parámetros**:
   - Tabla con todas las operaciones registradas en SQLite y visualizador de parámetros de riesgo.

---

## 🚀 Inicio Rápido con Interfaz Gráfica

### 1. Instalar dependencias
```bash
pip install -r requirements.txt
```

### 2. Ejecutar el Bot y abrir el Dashboard
```bash
python main.py
```
> Abre tu navegador en **`http://localhost:8000`** para interactuar con la interfaz gráfica.

---

## ⚙️ Opciones de Ejecución por Línea de Comandos

- **Cambiar el puerto del Dashboard:**
  ```bash
  python main.py --port 8080
  ```
- **Ejecutar en modo consola clásico (sin interfaz web):**
  ```bash
  python main.py --no-gui
  ```
- **Ejecutar una sola iteración de prueba:**
  ```bash
  python main.py --once
  ```

---

## 🧪 Pruebas Unitarias

Para ejecutar la suite de pruebas:
```bash
pytest tests/ -v
```
