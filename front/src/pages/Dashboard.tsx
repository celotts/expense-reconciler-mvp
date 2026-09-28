/** Dashboard: resumen ejecutivo de la actividad del sistema.
 *
 *  No es un catálogo ni una tabla. Es lo que una persona necesita ver al
 *  abrir la app para saber "qué pasa" en 10 segundos:
 *
 *  1. HOY: tickets procesados, monto, qué está pendiente de revisar.
 *  2. MES actual vs anterior: tendencia de actividad.
 *  3. AÑO actual vs anterior: visión anual.
 *  4. Cola de revisión AHORA: acción inmediata.
 *  5. Exactitud (SLO 96%): solo si hay empresa seleccionada.
 *
 *  Si no hay empresa seleccionada, muestra totales globales y un selector
 *  para filtrar. Si hay una, todo es para esa empresa.
 */
import { useState, useEffect } from 'react';
import { dashboardApi, type DashboardResponse, type PeriodStats } from '../services/api';
import { companiesApi, type Company } from '../services/api';
import { useNavegacion } from '../contextos/Navegacion';
import { 
  Card, Loading, EmptyState, Badge, Select, Button 
} from '../components/ui';

const statusColors: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'default'> = {
  PENDIENTE: 'warning',
  RECHAZADO: 'danger',
  APROBADO: 'success',
  AUTO_APROBADO: 'info',
  PERFECT: 'success',
  MANUAL: 'warning',
  DISCREPANCY: 'danger',
};

function formatMoney(amount: number | string): string {
  const n = typeof amount === 'string' ? parseFloat(amount) : amount;
  return new Intl.NumberFormat('es-MX', { 
    style: 'currency', 
    currency: 'MXN',
    minimumFractionDigits: 2 
  }).format(n);
}

function formatNumber(n: number): string {
  return new Intl.NumberFormat('es-MX').format(n);
}

function PeriodCard({ title, stats, accentColor = 'blue' }: { 
  title: string; 
  stats: PeriodStats; 
  accentColor?: string;
}) {
  const colorClasses: Record<string, string> = {
    blue: 'bg-blue-50 border-blue-200 text-blue-800',
    green: 'bg-green-50 border-green-200 text-green-800',
    yellow: 'bg-yellow-50 border-yellow-200 text-yellow-800',
    red: 'bg-red-50 border-red-200 text-red-800',
    purple: 'bg-purple-50 border-purple-200 text-purple-800',
  };
  const accent = colorClasses[accentColor] || colorClasses.blue;
  
  return (
    <Card className={`border-l-4 ${accent.replace('text-', 'border-')}`}>
      <h3 className="text-sm font-medium text-gray-500 mb-3">{title}</h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="p-3 bg-gray-50 rounded-lg">
          <p className="text-2xl font-bold text-gray-900">{formatNumber(stats.tickets_total)}</p>
          <p className="text-xs text-gray-500">Tickets</p>
        </div>
        <div className="p-3 bg-gray-50 rounded-lg">
          <p className="text-2xl font-bold text-gray-900">{formatMoney(stats.monto_total)}</p>
          <p className="text-xs text-gray-500">Monto total</p>
        </div>
        <div className="p-3 bg-gray-50 rounded-lg">
          <p className="text-2xl font-bold text-amber-700">{formatNumber(stats.tickets_pendientes)}</p>
          <p className="text-xs text-gray-500">Pendientes</p>
        </div>
        <div className="p-3 bg-gray-50 rounded-lg">
          <p className="text-2xl font-bold text-red-700">{formatMoney(stats.monto_pendiente)}</p>
          <p className="text-xs text-gray-500">Monto pendiente</p>
        </div>
      </div>
      
      <div className="mt-4 pt-4 border-t border-gray-200">
        <div className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <p className="text-gray-500">Mov. banco</p>
            <p className="font-medium">{formatNumber(stats.bank_transactions)}</p>
          </div>
          <div>
            <p className="text-gray-500">PERFECT</p>
            <p className="font-medium text-green-700">{formatNumber(stats.reconciliations_perfect)}</p>
          </div>
          <div>
            <p className="text-gray-500">MANUAL</p>
            <p className="font-medium text-amber-700">{formatNumber(stats.reconciliations_manual)}</p>
          </div>
          <div className="col-span-3 flex flex-wrap gap-2 mt-2">
            <Badge variant={statusColors.APROBADO || 'success'}>
              Aprobados: {stats.tickets_aprobados}
            </Badge>
            <Badge variant={statusColors.AUTO_APROBADO || 'info'}>
              Auto: {stats.tickets_auto_aprobados}
            </Badge>
            <Badge variant={statusColors.RECHAZADO || 'danger'}>
              Rechazados: {stats.tickets_rechazados}
            </Badge>
            <Badge variant={statusColors.DISCREPANCY || 'danger'}>
              Discrepancias: {stats.reconciliations_discrepancy}
            </Badge>
          </div>
        </div>
      </div>
    </Card>
  );
}

function ReviewQueueCard({ queue }: { queue: Record<string, number> }) {
  const total = Object.values(queue).reduce((a, b) => a + b, 0);
  if (total === 0) {
    return (
      <Card className="bg-green-50 border-green-200">
        <div className="flex items-center justify-center py-8">
          <div className="text-center">
            <svg className="w-12 h-12 mx-auto text-green-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.745 3.745 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" />
            </svg>
            <p className="mt-2 text-green-700 font-medium">Cola de revisión vacía</p>
            <p className="text-sm text-green-600">No hay tickets pendientes de revisión humana</p>
          </div>
        </div>
      </Card>
    );
  }
  return (
    <Card>
      <h3 className="text-lg font-semibold mb-3">Cola de revisión (ahora)</h3>
      <div className="flex flex-wrap gap-2 mb-4">
        {Object.entries(queue).map(([status, count]) => (
          <Badge key={status} variant={statusColors[status] || 'default'}>
            {status}: {count}
          </Badge>
        ))}
      </div>
      <Button variant="primary" size="sm" onClick={() => {}}>
        Ir a cola de revisión
      </Button>
    </Card>
  );
}

function AccuracyCard({ exactitud }: { exactitud: DashboardResponse['exactitud'] }) {
  if (!exactitud) {
    return (
      <Card>
        <h3 className="text-lg font-semibold mb-3">Exactitud (SLO 96%)</h3>
        <p className="text-gray-500 text-sm">
          Selecciona una empresa para ver el reporte de exactitud
        </p>
      </Card>
    );
  }
  
  const verdictColors: Record<string, 'success' | 'warning' | 'danger' | 'info' | 'default'> = {
    CUMPLE: 'success',
    NO_CUMPLE: 'danger',
    INCONCLUYENTE: 'warning',
    SIN_EVIDENCIA: 'default',
  };
  
  return (
    <Card>
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-lg font-semibold">Exactitud (SLO 96%)</h3>
        <Badge variant={verdictColors[exactitud.veredicto_global] || 'default'}>
          {exactitud.veredicto_global}
        </Badge>
      </div>
      <p className="text-sm text-gray-600 mb-4">{exactitud.explicacion}</p>
      <div className="space-y-2">
        {exactitud.por_origen.map((o: any) => (
          <div key={o.origen} className="p-3 bg-gray-50 rounded-lg">
            <div className="flex items-center justify-between mb-1">
              <span className="font-medium">{o.origen}</span>
              <Badge variant={verdictColors[o.veredicto] || 'default'}>{o.veredicto}</Badge>
            </div>
            <div className="text-sm text-gray-600 flex gap-4">
              <span>Aciertos: {o.aciertos}/{o.revisados}</span>
              {o.exactitud !== null && (
                <span>Exactitud: {(o.exactitud * 100).toFixed(1)}%</span>
              )}
              {o.intervalo_inferior !== null && o.intervalo_superior !== null && (
                <span>IC 95%: [{(o.intervalo_inferior * 100).toFixed(1)}%, {(o.intervalo_superior * 100).toFixed(1)}%]</span>
              )}
              {o.campo_mas_fallido && (
                <span className="text-red-600">Campo top: {o.campo_mas_fallido}</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function Dashboard() {
  const { irA } = useNavegacion();
  const [dashboard, setDashboard] = useState<DashboardResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [companies, setCompanies] = useState<Company[]>([]);
  const [selectedCompany, setSelectedCompany] = useState<string>('');

  const loadDashboard = async () => {
    try {
      setLoading(true);
      setError(null);
      const data = await dashboardApi.get(selectedCompany || undefined);
      setDashboard(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Error al cargar dashboard');
    } finally {
      setLoading(false);
    }
  };

  const loadCompanies = async () => {
    try {
      const data = await companiesApi.list();
      setCompanies(data);
    } catch {
      // Silencioso
    }
  };

  useEffect(() => {
    loadCompanies();
    loadDashboard();
  }, [selectedCompany]);

  if (loading) {
    return <Loading message="Cargando dashboard..." />;
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg">
          {error}
          <Button variant="ghost" size="sm" className="ml-4" onClick={loadDashboard}>
            Reintentar
          </Button>
        </div>
      </div>
    );
  }

  if (!dashboard) {
    return <EmptyState message="No hay datos de dashboard" />;
  }

  return (
    <div className="p-6 space-y-6">
      {/* Header con selector de empresa */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
          <p className="text-gray-500">
            {dashboard.company_name 
              ? `Empresa: ${dashboard.company_name}` 
              : 'Vista global (todas las empresas)'
            }
          </p>
        </div>
        <Select
          label="Empresa"
          value={selectedCompany}
          onChange={e => setSelectedCompany(e.target.value)}
          options={[
            { value: '', label: 'Todas las empresas (global)' },
            ...companies.map(c => ({ value: c.id, label: `${c.name} (${c.tax_id})` }))
          ]}
        />
      </div>

      {/* KPIs principales - HOY */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <PeriodCard title="Hoy" stats={dashboard.hoy} accentColor="blue" />
        <PeriodCard title="Mes actual" stats={dashboard.mes_actual} accentColor="green" />
        <PeriodCard title="Mes anterior" stats={dashboard.mes_anterior} accentColor="yellow" />
        <PeriodCard title="Año actual" stats={dashboard.ano_actual} accentColor="purple" />
      </div>

      {/* Segunda fila: Año anterior + Totales + Cola + Exactitud */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <PeriodCard title="Año anterior" stats={dashboard.ano_anterior} accentColor="red" />
        <PeriodCard title="Totales acumulados" stats={dashboard.totales_acumulados} accentColor="blue" />
        <div className="md:col-span-2 lg:col-span-2">
          <div className="grid gap-4 md:grid-cols-2">
            <ReviewQueueCard queue={dashboard.review_queue} />
            <AccuracyCard exactitud={dashboard.exactitud} />
          </div>
        </div>
      </div>

      {/* Acciones rápidas */}
      <Card>
        <h3 className="text-lg font-semibold mb-4">Acciones rápidas</h3>
        <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-4">
          <Button variant="primary" className="h-20 flex flex-col items-center justify-center gap-2" onClick={() => irA('tickets')}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 6v6m0 0v6m0-6h6m-6 0H6" /></svg>
            <span>Nuevo ticket</span>
          </Button>
          <Button variant="secondary" className="h-20 flex flex-col items-center justify-center gap-2" onClick={() => irA('bank')}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
            <span>Subir extracto</span>
          </Button>
          <Button variant="secondary" className="h-20 flex flex-col items-center justify-center gap-2" onClick={() => irA('reconciliations')}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4" /></svg>
            <span>Conciliar</span>
          </Button>
          <Button variant="secondary" className="h-20 flex flex-col items-center justify-center gap-2" onClick={() => irA('review')}>
            <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 12.75L11.25 15 15 9.75M21 12c0 1.268-.63 2.39-1.593 3.068a3.745 3.745 0 01-1.043 3.296 3.745 3.745 0 01-3.296 1.043A3.745 3.745 0 0112 21c-1.268 0-2.39-.63-3.068-1.593a3.746 3.746 0 01-3.296-1.043 3.745 3.745 0 01-1.043-3.296A3.745 3.745 0 013 12c0-1.268.63-2.39 1.593-3.068a3.745 3.745 0 011.043-3.296 3.746 3.746 0 013.296-1.043A3.745 3.745 0 0112 3c1.268 0 2.39.63 3.068 1.593a3.746 3.746 0 013.296 1.043 3.746 3.746 0 011.043 3.296A3.745 3.745 0 0121 12z" /></svg>
            <span>Revisar cola</span>
          </Button>
        </div>
      </Card>
    </div>
  );
}