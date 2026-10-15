import React, { useEffect, useState } from 'react';
import { SecureAPI } from '../lib/secureApi';

interface RevenueData {
  property_id: string;
  total_revenue: string;
  currency: string;
  reservations_count: number;
  timezone: string;
  month: number | null;
  year: number | null;
}

interface RevenueSummaryProps {
  propertyId: string;
  month?: number;
  year?: number;
  currency?: string;
  showRaw?: boolean;
}

export const RevenueSummary: React.FC<RevenueSummaryProps> = ({ propertyId, month, year, currency, showRaw }) => {
  const [data, setData] = useState<RevenueData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setData(null);
    setError('');
    SecureAPI.getDashboardSummary(propertyId, { month, year, currency })
      .then((response) => { if (!cancelled) setData(response); })
      .catch(() => { if (!cancelled) setError('Failed to load revenue data. Please refresh to try again.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [propertyId, month, year, currency]);

  if (loading) return <div role="status" className="p-6 animate-pulse">Loading revenue…</div>;
  if (error) return <div role="alert" className="p-4 text-red-600 bg-red-50 rounded-lg">{error}</div>;
  if (!data) return null;

  // The server returns the final rounded decimal string. Format without float conversion.
  const [whole, fraction] = data.total_revenue.split('.');
  const displayTotal = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + '.' + fraction;
  const periodLabel = data.month
    ? `${data.year}-${String(data.month).padStart(2, '0')}`
    : data.year ? String(data.year) : 'All time';

  return (
    <div className="bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
      {showRaw && <pre className="p-3 bg-gray-50 text-xs">{JSON.stringify(data, null, 2)}</pre>}
      <div className="p-6">
        <h2 className="text-sm font-medium text-gray-500 uppercase tracking-wide">Total Revenue</h2>
        <p className="text-3xl font-bold text-gray-900 mt-1">{data.currency} {displayTotal}</p>
        <p className="text-sm text-gray-500 mt-2">{periodLabel} · {data.timezone}</p>
        <div className="grid grid-cols-2 gap-4 mt-6 pt-4 border-t border-gray-100">
          <div>
            <p className="text-xs text-gray-500 uppercase">Property ID</p>
            <p className="text-sm font-semibold mt-1">{data.property_id}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500 uppercase">Reservations</p>
            <p className="text-sm font-semibold mt-1">{data.reservations_count} bookings</p>
          </div>
        </div>
      </div>
    </div>
  );
};
