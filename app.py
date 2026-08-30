import streamlit as st
import pandas as pd
from ortools.sat.python import cp_model
import json
import calendar
from datetime import date

st.set_page_config(page_title="Gestor Guardias V7", layout="wide")

# Inicialización de Base de Datos en sesión
if 'db' not in st.session_state:
    st.session_state.db = {'residentes': [], 'prefs': {}, 'historial': {}, 'ajustes': {'num_res': 2}}

def get_dias_mes(year, month):
    _, num_days = calendar.monthrange(year, month)
    dias = []
    for d in range(1, num_days + 1):
        wd = date(year, month, d).weekday()
        dias.append({'dia': d, 'wd': wd, 'es_F': wd >= 5})
    return dias

def motor_ortools(residentes, prefs, dias_mes, ajustes, historial):
    model = cp_model.CpModel()
    num_dias = len(dias_mes)
    
    # Variables: x[(r, d)] = 1 si el residente r trabaja el día d
    x = {}
    for r in residentes:
        for d in dias_mes:
            x[(r['id'], d['dia'])] = model.NewBoolVar(f"x_{r['id']}_{d['dia']}")
            
    # RESTRICCIONES DURAS (Hard Constraints)
    for d in dias_mes:
        # 1. Cobertura: Exactamente N residentes por día
        model.Add(sum(x[(r['id'], d['dia'])] for r in residentes) == ajustes['num_res'])
        
        # 2. Supervisión R1: Si hay un R1, tiene que haber un mayor (R3, R4 o R5)
        r1_vars = [x[(r['id'], d['dia'])] for r in residentes if r['nivel'] == 'R1']
        mayores_vars = [x[(r['id'], d['dia'])] for r in residentes if r['nivel'] in ['R3', 'R4', 'R5']]
        if r1_vars and mayores_vars:
            for r1 in r1_vars:
                model.AddImplication(r1, sum(mayores_vars) >= 1)
                
    for r in residentes:
        p = prefs.get(r['id'], {'vacas': [], 'bloqueos': [], 'minL': 0, 'maxL': 10, 'minF': 0, 'maxF': 5})
        
        # 3. Vacaciones y Bloqueos
        for d in p['vacas'] + p['bloqueos']:
            if d <= num_dias:
                model.Add(x[(r['id'], d)] == 0)
                
        # 4. Salientes: No trabajar dos días seguidos
        for d in range(1, num_dias):
            model.Add(x[(r['id'], d)] + x[(r['id'], d+1)] <= 1)
            
        # 5. Saliente post-fin de semana: Si trabaja Sábado (wd=5) o Domingo (wd=6), no trabaja Lunes (wd=0)
        for d in dias_mes:
            if d['wd'] in [5, 6]: 
                lunes = d['dia'] + (7 - d['wd'])
                if lunes <= num_dias:
                    model.AddImplication(x[(r['id'], d['dia'])], x[(r['id'], lunes)] == 0)

    # RESTRICCIONES BLANDAS Y OBJETIVOS (Soft Constraints)
    # Se usan variables enteras para calcular desviaciones y penalizarlas
    penalizaciones = []
    
    for r in residentes:
        p = prefs.get(r['id'], {'vacas': [], 'bloqueos': [], 'minL': 0, 'maxL': 10, 'minF': 0, 'maxF': 5})
        saldo = historial.get(r['id'], 0) # Positivo = le deben guardias (debe hacer menos)
        
        guardias_L = sum(x[(r['id'], d['dia'])] for d in dias_mes if not d['es_F'])
        guardias_F = sum(x[(r['id'], d['dia'])] for d in dias_mes if d['es_F'])
        
        # Rangos ajustados por saldo histórico
        obj_minL = max(0, p['minL'] - saldo) if saldo > 0 else p['minL']
        obj_maxL = p['maxL'] + abs(saldo) if saldo < 0 else p['maxL']
        
        # Penalizar desviación de laborables
        dev_L = model.NewIntVar(-31, 31, f"devL_{r['id']}")
        abs_dev_L = model.NewIntVar(0, 31, f"abs_devL_{r['id']}")
        model.Add(dev_L == guardias_L - obj_minL)
        model.AddAbsEquality(abs_dev_L, dev_L)
        penalizaciones.append(abs_dev_L * 100) # Peso alto para cumplir el rango
        
        # Penalizar desviación de festivos
        dev_F = model.NewIntVar(-31, 31, f"devF_{r['id']}")
        abs_dev_F = model.NewIntVar(0, 31, f"abs_devF_{r['id']}")
        model.Add(dev_F == guardias_F - p['minF'])
        model.AddAbsEquality(abs_dev_F, dev_F)
        penalizaciones.append(abs_dev_F * 150) # Festivos son más críticos

    # Minimizar las penalizaciones
    model.Minimize(sum(penalizaciones))
    
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 8.0 # Tiempo máximo de búsqueda de la IA
    status = solver.Solve(model)
    
    if status == cp_model.OPTIMAL or status == cp_model.FEASIBLE:
        cuadrante = {}
        for d in dias_mes:
            cuadrante[d['dia']] = [r['nombre'] for r in residentes if solver.Value(x[(r['id'], d['dia'])]) == 1]
        return cuadrante, status
    else:
        return None, status

# --- INTERFAZ STREAMLIT ---
st.title("🏥 Motor OR-Tools: Reparto de Guardias")

# Mock data para la demo rápida (Esto se conectaría a tu UI)
if not st.session_state.db['residentes']:
    st.session_state.db['residentes'] = [
        {'id': '1', 'nombre': 'Ana', 'nivel': 'R5'},
        {'id': '2', 'nombre': 'Juan', 'nivel': 'R1'},
        {'id': '3', 'nombre': 'Luis', 'nivel': 'R3'}
    ]
    st.session_state.db['prefs'] = {
        '1': {'vacas': [15, 16], 'bloqueos': [], 'minL': 2, 'maxL': 3, 'minF': 1, 'maxF': 1},
        '2': {'vacas': [], 'bloqueos': [5], 'minL': 4, 'maxL': 4, 'minF': 2, 'maxF': 2},
        '3': {'vacas': [], 'bloqueos': [], 'minL': 3, 'maxL': 4, 'minF': 1, 'maxF': 2}
    }

mes = st.date_input("Mes a planificar", date.today())
dias = get_dias_mes(mes.year, mes.month)

if st.button("🚀 Ejecutar Algoritmo Óptimo (OR-Tools)", type="primary"):
    with st.spinner("Calculando millones de combinaciones con Inteligencia Artificial..."):
        cuadrante, status = motor_ortools(
            st.session_state.db['residentes'], 
            st.session_state.db['prefs'], 
            dias, 
            st.session_state.db['ajustes'],
            st.session_state.db['historial']
        )
        
        if cuadrante:
            st.success(f"¡Reparto completado! Estado matemático: {solver.StatusName(status)}")
            df = pd.DataFrame([{"Día": d, "Residentes": ", ".join(res)} for d, res in cuadrante.items()])
            st.dataframe(df, use_container_width=True)
        else:
            st.error("❌ Imposible matemáticamente. Hay demasiados bloqueos o vacaciones y no se pueden cubrir los turnos respetando los salientes.")