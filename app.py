import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import calendar
from datetime import date
import json
import io

st.set_page_config(page_title="Gestor Guardias - Psiquiatría", page_icon="🏥", layout="wide")

# --- 1. GESTIÓN DE ESTADO Y MEMORIA ---
# En la nube gratuita, el servidor se "duerme" si no lo usas. 
# Todo se guarda en st.session_state mientras lo usas.
if 'db' not in st.session_state:
    st.session_state.db = {
        'residentes': [],
        'prefs': {},
        'historial_saldos': {}, 
        'asignaciones_fijas': {}, 
        'ajustes': {'num_res': 2, 'pesos': {'vacas': 1000, 'saliente': 1000, 'rangos': 50, 'r1': 1000}}
    }

def parsear_dias(texto):
    if not texto or pd.isna(texto): return []
    dias = set()
    for parte in str(texto).split(','):
        parte = parte.strip()
        if '-' in parte:
            try:
                inicio, fin = map(int, parte.split('-'))
                dias.update(range(inicio, fin + 1))
            except: pass
        else:
            try: dias.add(int(parte))
            except: pass
    return sorted(list(dias))

def parsear_rango(texto, default_min, default_max):
    if not texto or pd.isna(texto): return default_min, default_max
    texto = str(texto).strip()
    if '-' in texto:
        try:
            inicio, fin = map(int, texto.split('-'))
            return inicio, fin
        except: pass
    try:
        val = int(texto)
        return val, val
    except:
        return default_min, default_max

# --- 2. INTERFAZ GRÁFICA ---
st.title("🏥 Gestor de Guardias PRO - Psiquiatría")

# Selector Global
col_m, col_a, _, _ = st.columns(4)
meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
mes_actual = col_m.selectbox("Mes a planificar", range(1, 13), index=date.today().month % 12, format_func=lambda x: meses[x-1])
año_actual = col_a.number_input("Año", value=2026, min_value=2024, max_value=2030)

_, num_dias = calendar.monthrange(año_actual, mes_actual)
dias_mes = [{'dia': d, 'wd': date(año_actual, mes_actual, d).weekday(), 'es_F': date(año_actual, mes_actual, d).weekday() >= 5} for d in range(1, num_dias + 1)]

tab_perfiles, tab_prefs, tab_reparto, tab_ajustes = st.tabs(["👥 1. Perfiles", "📝 2. Preferencias", "🗓️ 3. Reparto Óptimo", "⚙️ 4. Ajustes y Backup"])

# --- TAB 1: PERFILES ---
with tab_perfiles:
    st.header("Gestión de Residentes")
    
    with st.form("form_nuevo_residente"):
        c1, c2, c3 = st.columns(3)
        apodo = c1.text_input("Apodo (App)")
        nombre_completo = c2.text_input("Nombre Completo Oficial")
        nivel = c3.selectbox("Nivel", ["R1", "R2", "R3", "R4", "R5"])
        if st.form_submit_button("➕ Añadir Residente"):
            if apodo:
                st.session_state.db['residentes'].append({'id': apodo, 'apodo': apodo, 'nombre_completo': nombre_completo, 'nivel': nivel})
                st.success(f"{apodo} añadido.")
                st.rerun()

    if st.session_state.db['residentes']:
        df_res = pd.DataFrame(st.session_state.db['residentes'])
        # Añadir columna de saldo histórico editable
        df_res['Saldo Histórico'] = df_res['id'].apply(lambda x: st.session_state.db['historial_saldos'].get(x, 0))
        st.dataframe(df_res, use_container_width=True)
        
        if st.button("🗑️ Borrar todos los residentes", type="secondary"):
            st.session_state.db['residentes'] = []
            st.rerun()

# --- TAB 2: PREFERENCIAS ---
with tab_prefs:
    st.header(f"Preferencias para {meses[mes_actual-1]} {año_actual}")
    st.info("💡 **Formato:** Rangos (ej. `3-4`), Días (ej. `1, 2-5, 8`).")
    
    if st.session_state.db['residentes']:
        # Preparar dataframe interactivo
        datos_prefs = []
        for r in st.session_state.db['residentes']:
            p = st.session_state.db['prefs'].get(r['id'], {})
            datos_prefs.append({
                'Residente': r['apodo'],
                'Obj L (ej: 3-4)': p.get('rango_L', '3-4'),
                'Obj F (ej: 1-2)': p.get('rango_F', '1'),
                'Vacaciones (días)': p.get('vacas', ''),
                'Bloqueos (días)': p.get('bloqueos', '')
            })
        
        df_prefs_input = pd.DataFrame(datos_prefs)
        df_editado = st.data_editor(df_prefs_input, use_container_width=True, hide_index=True)
        
        if st.button("💾 Guardar Preferencias", type="primary"):
            for index, row in df_editado.iterrows():
                res_id = st.session_state.db['residentes'][index]['id']
                st.session_state.db['prefs'][res_id] = {
                    'rango_L': row['Obj L (ej: 3-4)'],
                    'rango_F': row['Obj F (ej: 1-2)'],
                    'vacas': row['Vacaciones (días)'],
                    'bloqueos': row['Bloqueos (días)']
                }
            st.success("Preferencias guardadas correctamente.")
    else:
        st.warning("Añade residentes en la pestaña Perfiles primero.")

# --- TAB 3: REPARTO ÓPTIMO ---
with tab_reparto:
    st.header("Generación de Cuadrante")
    
    col_izq, col_der = st.columns([2, 1])
    
    with col_izq:
        st.subheader("1. Asignaciones Manuales a Dedo (Fijar)")
        st.caption("Si alguien debe cubrir un día obligatoriamente, escríbelo aquí antes de generar el reparto.")
        
        # Grid para fijar a dedo
        dias_lista = [d['dia'] for d in dias_mes]
        df_fijos = pd.DataFrame({"Día": dias_lista})
        nombres_res = [r['apodo'] for r in st.session_state.db['residentes']]
        
        # Inicializar columnas según el número de residentes por guardia configurado
        for i in range(st.session_state.db['ajustes']['num_res']):
            df_fijos[f"Puesto {i+1}"] = [st.session_state.db['asignaciones_fijas'].get(d, {}).get(f"P{i+1}", None) for d in dias_lista]
            
        fijos_editado = st.data_editor(
            df_fijos, 
            column_config={f"Puesto {i+1}": st.column_config.SelectboxColumn(options=[None] + nombres_res) for i in range(st.session_state.db['ajustes']['num_res'])},
            disabled=["Día"],
            hide_index=True,
            use_container_width=True
        )

        st.subheader("2. Lanzar Algoritmo")
        if st.button("🚀 GENERAR OPCIÓN MÁS ÓPTIMA", type="primary", use_container_width=True):
            
            # 1. Guardar los fijos editados
            st.session_state.db['asignaciones_fijas'] = {}
            for idx, row in fijos_editado.iterrows():
                dia = row['Día']
                fijos_dia = {}
                for i in range(st.session_state.db['ajustes']['num_res']):
                    if row[f"Puesto {i+1}"]:
                        fijos_dia[f"P{i+1}"] = row[f"Puesto {i+1}"]
                if fijos_dia:
                    st.session_state.db['asignaciones_fijas'][dia] = fijos_dia
            
            # 2. Preparar el motor OR-Tools
            with st.spinner("🧠 Evaluando permutaciones y resolviendo restricciones duras..."):
                model = cp_model.CpModel()
                residentes = st.session_state.db['residentes']
                x = {} # Variables de decisión
                
                for r in residentes:
                    for d in dias_mes:
                        x[(r['id'], d['dia'])] = model.NewBoolVar(f"x_{r['id']}_{d['dia']}")
                
                # --- RESTRICCIONES DURAS ---
                for d in dias_mes:
                    # Cobertura exacta
                    model.Add(sum(x[(r['id'], d['dia'])] for r in residentes) == st.session_state.db['ajustes']['num_res'])
                    
                    # Regla R1: Nunca solo con un R1 o R2. Debe haber un mayor (R3, R4, R5)
                    r1_vars = [x[(r['id'], d['dia'])] for r in residentes if r['nivel'] == 'R1']
                    mayores_vars = [x[(r['id'], d['dia'])] for r in residentes if r['nivel'] in ['R3', 'R4', 'R5']]
                    if r1_vars and mayores_vars:
                        for r1 in r1_vars:
                            model.AddImplication(r1, sum(mayores_vars) >= 1)
                            
                    # Aplicar asignaciones a dedo
                    fijos_hoy = st.session_state.db['asignaciones_fijas'].get(d['dia'], {})
                    for p_val in fijos_hoy.values():
                        res_fijo = next((r for r in residentes if r['apodo'] == p_val), None)
                        if res_fijo:
                            model.Add(x[(res_fijo['id'], d['dia'])] == 1)
                            
                for r in residentes:
                    p = st.session_state.db['prefs'].get(r['id'], {})
                    vacas = parsear_dias(p.get('vacas', ''))
                    bloqueos = parsear_dias(p.get('bloqueos', ''))
                    
                    # Vacaciones y Bloqueos
                    for d in vacas + bloqueos:
                        if d <= num_dias:
                            model.Add(x[(r['id'], d)] == 0)
                            
                    # Salientes directos (No trabajar 2 días seguidos)
                    for d in range(1, num_dias):
                        model.Add(x[(r['id'], d)] + x[(r['id'], d+1)] <= 1)
                        
                    # Saliente post-fin de semana (Blindaje del Lunes)
                    for d in dias_mes:
                        if d['wd'] in [5, 6]: # Sabado o Domingo
                            lunes = d['dia'] + (7 - d['wd'])
                            if lunes <= num_dias:
                                model.AddImplication(x[(r['id'], d['dia'])], x[(r['id'], lunes)] == 0)

                # --- RESTRICCIONES BLANDAS Y OPTIMIZACIÓN ---
                penalizaciones = []
                for r in residentes:
                    p = st.session_state.db['prefs'].get(r['id'], {})
                    min_L, max_L = parsear_rango(p.get('rango_L', ''), 0, 10)
                    min_F, max_F = parsear_rango(p.get('rango_F', ''), 0, 5)
                    
                    guardias_L = sum(x[(r['id'], d['dia'])] for d in dias_mes if not d['es_F'])
                    guardias_F = sum(x[(r['id'], d['dia'])] for d in dias_mes if d['es_F'])
                    
                    # Saldo histórico (compensación)
                    saldo = st.session_state.db['historial_saldos'].get(r['id'], 0)
                    target_L = max(0, min_L - saldo) if saldo > 0 else max_L + abs(saldo) if saldo < 0 else min_L
                    
                    # Variables de desviación
                    diff_L = model.NewIntVar(-31, 31, f"diff_L_{r['id']}")
                    abs_L = model.NewIntVar(0, 31, f"abs_L_{r['id']}")
                    model.Add(diff_L == guardias_L - target_L)
                    model.AddAbsEquality(abs_L, diff_L)
                    penalizaciones.append(abs_L * st.session_state.db['ajustes']['pesos']['rangos'])
                    
                    diff_F = model.NewIntVar(-31, 31, f"diff_F_{r['id']}")
                    abs_F = model.NewIntVar(0, 31, f"abs_F_{r['id']}")
                    model.Add(diff_F == guardias_F - min_F)
                    model.AddAbsEquality(abs_F, diff_F)
                    penalizaciones.append(abs_F * (st.session_state.db['ajustes']['pesos']['rangos'] + 50))

                model.Minimize(sum(penalizaciones))
                
                # --- RESOLUCIÓN ---
                solver = cp_model.CpSolver()
                solver.parameters.max_time_in_seconds = 10.0
                status = solver.Solve(model)
                
                if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
                    st.success("✅ ¡Cuadrante matemático generado con éxito!")
                    
                    cuadrante = []
                    dias_nombres = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
                    
                    for d in dias_mes:
                        trabajadores = [r for r in residentes if solver.Value(x[(r['id'], d['dia'])]) == 1]
                        fila = {
                            "Día": d['dia'], 
                            "Semana": dias_nombres[d['wd']], 
                            "Tipo": "🔴 F" if d['es_F'] else "L"
                        }
                        for i in range(st.session_state.db['ajustes']['num_res']):
                            if i < len(trabajadores):
                                res = trabajadores[i]
                                fila[f"Puesto {i+1}"] = f"{res['nombre_completo'] if res.get('nombre_completo') else res['apodo']} ({res['nivel']})"
                            else:
                                fila[f"Puesto {i+1}"] = "-"
                        cuadrante.append(fila)
                    
                    df_final = pd.DataFrame(cuadrante)
                    st.session_state['df_final'] = df_final # Guardar para exportar
                    st.dataframe(df_final.style.apply(lambda x: ['background-color: #fef2f2' if x['Tipo'] == '🔴 F' else '' for i in x], axis=1), use_container_width=True)
                    
                else:
                    st.error("❌ Imposible matemáticamente. Relaja los bloqueos, vacaciones o permite más residentes.")

    with col_der:
        st.subheader("📥 Exportación")
        if 'df_final' in st.session_state:
            # Generar Excel en memoria
            buffer = io.BytesIO()
            with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                st.session_state['df_final'].to_excel(writer, index=False, sheet_name='Cuadrante')
            
            st.download_button(
                label="📊 Descargar Cuadrante Oficial (.xlsx)",
                data=buffer.getvalue(),
                file_name=f"Cuadrante_Psiquiatria_{meses[mes_actual-1]}_{año_actual}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                use_container_width=True
            )
        else:
            st.info("Genera el cuadrante primero para poder exportarlo a Excel.")

# --- TAB 4: AJUSTES Y BACKUP ---
with tab_ajustes:
    st.header("Configuración del Sistema")
    c1, c2 = st.columns(2)
    
    with c1:
        st.subheader("⚙️ Reglas del Motor")
        num_res = st.number_input("Residentes por guardia (Tamaño de turno)", min_value=1, max_value=5, value=st.session_state.db['ajustes']['num_res'])
        if st.button("Actualizar Reglas"):
            st.session_state.db['ajustes']['num_res'] = num_res
            st.success("Reglas actualizadas.")

    with c2:
        st.subheader("💾 Backup y Restauración")
        st.caption("Descarga toda la base de datos (residentes, historial, ajustes) en un archivo JSON para no perderla si se reinicia el servidor.")
        
        # Exportar
        json_str = json.dumps(st.session_state.db, ensure_ascii=False, indent=2)
        st.download_button(label="📥 Descargar Backup (.json)", data=json_str, file_name=f"Backup_Guardias_{date.today()}.json", mime="application/json")
        
        st.divider()
        # Importar
        archivo_subido = st.file_uploader("Restaurar desde archivo (.json)", type=["json"])
        if archivo_subido is not None:
            if st.button("⚠️ Sobrescribir sistema con este Backup", type="primary"):
                datos = json.load(archivo_subido)
                st.session_state.db = datos
                st.success("Sistema restaurado con éxito.")
                st.rerun()
