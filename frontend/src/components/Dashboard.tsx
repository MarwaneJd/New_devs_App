import React, { useEffect, useState } from "react";
import { RevenueSummary } from "./RevenueSummary";
import { SecureAPI } from "../lib/secureApi";
import { useAuth } from "../contexts/AuthContext.new";

interface Property {
  id: string;
  name: string;
  timezone: string;
  currencies: string[];
}

const Dashboard: React.FC = () => {
  const { user } = useAuth();
  const [properties, setProperties] = useState<Property[]>([]);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [selectedProperty, setSelectedProperty] = useState("");
  const [period, setPeriod] = useState("all");
  const [reportMonth, setReportMonth] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });
  const [reportYear, setReportYear] = useState(String(new Date().getFullYear()));
  const [currency, setCurrency] = useState("");

  useEffect(() => {
    let cancelled = false;
    setLoadedFor(null);
    setError("");
    SecureAPI.getDashboardProperties().then((items) => {
      if (cancelled) return;
      setProperties(items);
      setSelectedProperty(items[0]?.id || "");
      setCurrency(items[0]?.currencies[0] || "");
      setLoadedFor(user?.id || null);
    }).catch(() => {
      if (!cancelled) setError("Failed to load properties. Please refresh to try again.");
    });
    return () => { cancelled = true; };
  }, [user?.id]);

  const property = properties.find((item) => item.id === selectedProperty);
  const year = period === "month" ? Number(reportMonth.split("-")[0])
    : period === "year" ? Number(reportYear) : undefined;
  const month = period === "month" ? Number(reportMonth.split("-")[1]) : undefined;
  const validPeriod = period === "all" || (
    Number.isInteger(year) && year! >= 1 && year! <= 9998 &&
    (period !== "month" || (Number.isInteger(month) && month! >= 1 && month! <= 12))
  );
  const selectClass = "block px-3 py-2 border border-gray-300 rounded-md text-sm";

  return (
    <div className="p-4 lg:p-6 min-h-full">
      <div className="max-w-7xl mx-auto">
        <h1 className="text-2xl font-bold mb-6 text-gray-900">Property Management Dashboard</h1>
        <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 lg:p-6">
          <h2 className="text-xl font-medium text-gray-900 mb-2">Revenue Overview</h2>
          <p className="text-gray-600 mb-6">Revenue by check-in date in each property's local time zone.</p>
          {error ? <p role="alert" className="text-red-600">{error}</p>
            : loadedFor !== user?.id ? <p role="status">Loading properties…</p>
            : properties.length === 0 ? <p>No properties available for your account.</p>
            : <>
              <div className="flex flex-wrap gap-4 mb-6">
                <label className="text-sm text-gray-700">
                  Select Property
                  <select className={selectClass} value={selectedProperty} onChange={(event) => {
                    setSelectedProperty(event.target.value);
                    setCurrency(properties.find((item) => item.id === event.target.value)?.currencies[0] || "");
                  }}>
                    {properties.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
                  </select>
                </label>
                <label className="text-sm text-gray-700">
                  Report period
                  <select className={selectClass} value={period} onChange={(event) => setPeriod(event.target.value)}>
                    <option value="all">All time</option>
                    <option value="month">Monthly</option>
                    <option value="year">Annual</option>
                  </select>
                </label>
                {period === "month" && <label className="text-sm text-gray-700">
                  Reporting month
                  <input className={selectClass} type="month" min="0001-01" max="9998-12"
                    value={reportMonth} onChange={(event) => setReportMonth(event.target.value)} />
                </label>}
                {period === "year" && <label className="text-sm text-gray-700">
                  Reporting year
                  <input className={selectClass} type="number" min="1" max="9998"
                    value={reportYear} onChange={(event) => setReportYear(event.target.value)} />
                </label>}
                <label className="text-sm text-gray-700">
                  Currency
                  <select className={selectClass} value={currency} onChange={(event) => setCurrency(event.target.value)}>
                    {property?.currencies.map((value) => <option key={value} value={value}>{value}</option>)}
                  </select>
                </label>
              </div>
              {validPeriod ? <RevenueSummary
                key={JSON.stringify([user?.id, selectedProperty, year, month, currency])}
                propertyId={selectedProperty} year={year} month={month} currency={currency}
              /> : <p role="alert">Choose a valid reporting period.</p>}
            </>}
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
