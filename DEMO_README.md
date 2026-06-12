# Sistema Digital de Pase de Guardia con Triage Médico - Demo

## Descripción

Este sistema ha sido adaptado según el documento de requisitos para incluir:

### ✅ Características Implementadas

1. **Módulo de Triage Médico**
   - Sistema de clasificación en 5 niveles según protocolo institucional
   - Motor de reglas determinístico basado en signos vitales y síntomas
   - Visualización clara del nivel de triage en cada reporte
   - Tiempo máximo de atención según nivel

2. **Sistema de Alertas Mejorado**
   - Alertas por signos vitales fuera de rango
   - Detección de síntomas críticos en notas
   - Panel de "Últimas Novedades" con pacientes críticos
   - Clasificación automática por nivel de urgencia

3. **Vista por Unidad/Sala**
   - Agrupación de pacientes por unidad (Planta Baja, Planta Alta, Cuidados Paliativos)
   - Visualización de distribución de triage por unidad
   - Acceso rápido a pacientes desde la vista de unidad

4. **Visualización de Triage**
   - Badges de triage en cada reporte de enfermería
   - Código de colores: Rojo (T1), Naranja (T2), Amarillo (T3), Azul (T4), Verde (T5)
   - Información de triage en el panel de pacientes críticos

5. **Mejoras en la Interfaz**
   - Toggle entre vista de paciente individual y vista por unidad
   - Información de triage destacada en el hero del paciente
   - Mejor organización visual de la información crítica

## Niveles de Triage

- **TRIAGE 1 - Atención Inmediata**: Sat < 85%, Hipotensión severa, síntomas críticos
- **TRIAGE 2 - Alta Prioridad (10 min)**: Sat 85-90%, Alteraciones tensionales severas, fiebre alta
- **TRIAGE 3 - Prioridad Media (30 min)**: Sat 90-94%, Alteraciones moderadas, síntomas moderados
- **TRIAGE 4 - Prioridad Baja (60 min)**: Febrícula, consultas no urgentes
- **TRIAGE 5 - Rutina (120 min)**: Estado estable, controles de rutina

## Cómo Usar la Demo

### Vista de Paciente Individual
1. Buscar o seleccionar un paciente desde la lista
2. Ver historial cronológico con información de triage
3. Revisar signos vitales y notas de enfermería
4. Ver indicaciones médicas de farmacia

### Vista por Unidad
1. Hacer clic en "Vista por Unidad"
2. Ver todos los pacientes agrupados por unidad
3. Ver distribución de triage en cada unidad
4. Hacer clic en un paciente para ver su detalle

### Panel de Últimas Novedades
1. Hacer clic en el botón rojo "Últimas Novedades" (esquina inferior derecha)
2. Ver lista de pacientes críticos ordenados por triage y score de riesgo
3. Hacer clic en un paciente para ver su detalle completo

## API Endpoints Disponibles

- `GET /api/patients/list` - Lista de todos los pacientes
- `GET /api/reports/<patient_id>` - Reportes de un paciente específico (incluye triage)
- `GET /api/risk_radar` - Lista de pacientes críticos con triage
- `GET /api/patients/by_unit` - Pacientes agrupados por unidad con triage

## Próximos Pasos Sugeridos

Para una implementación completa según requisitos, se recomienda agregar:

1. **Gestión de Pacientes Completa**
   - Formulario de registro con todos los campos (DNI, edad, cama, diagnóstico)
   - Clasificación por tipo (Ingresos, Controles, UCIP, etc.)

2. **Plantillas de Indicaciones Médicas**
   - Interfaz para seleccionar plantillas predefinidas
   - Calculadora de dosis por peso
   - Historial de cambios en indicaciones

3. **Estudios Complementarios**
   - Carga estructurada de laboratorio
   - Carga de imágenes
   - Alertas de valores críticos

4. **Evolución Clínica Estructurada**
   - Campos estructurados para estado general, hemodinámico, respiratorio, neurológico
   - Escala de Glasgow
   - Balance hídrico

5. **Sistema de Usuarios y Seguridad**
   - Roles diferenciados (Médico, Enfermería, Jefe de guardia, Administrador)
   - Registro de auditoría
   - Historial de modificaciones

6. **Módulo de Feedback para IA**
   - Sistema de retroalimentación sobre sugerencias de triage
   - Métricas de desempeño del sistema

## Notas Técnicas

- El sistema utiliza datos existentes de WhatsApp exportados
- El triage se calcula automáticamente basado en signos vitales y texto de las notas
- Los datos se cargan desde `data/residentes_db.json`
- El sistema está listo para integrarse con una base de datos estructurada en el futuro

## Ejecución

```bash
python app.py
```

Luego abrir en el navegador: `http://localhost:5000`
