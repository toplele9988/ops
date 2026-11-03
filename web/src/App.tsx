import { SupplierDossiers, ProductDossiers, DossierGaps } from "./pages/Dossiers";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router";
import { AppShell } from "./layout/AppShell";
import { PLANNED_PAGES } from "./nav";
import { Channels } from "./pages/Channels";
import { AlibabaOpen } from "./pages/AlibabaOpen";
import { Pipeline } from "./pages/Pipeline";
import { Overview } from "./pages/Overview";
import { DealPage } from "./pages/DealPage";
import { Deals } from "./pages/Deals";
import { Freight } from "./pages/Freight";
import { Customers } from "./pages/Customers";
import { Inbox } from "./pages/Inbox";
import { LanguageSettings } from "./pages/LanguageSettings";
import { Inspiration } from "./pages/Inspiration";
import { KnowledgeHub } from "./pages/KnowledgeHub";
import { Packing } from "./pages/Packing";
import { AuditPage } from "./pages/Audit";
import { DirectoryPage } from "./pages/Directory";
import { ClassifyPage } from "./pages/Classify";
import { FulfilBoardPage } from "./pages/FulfilBoard";
import { LegacyPage } from "./pages/Legacy";
import { Guide } from "./pages/Guide";
import { Operator } from "./pages/Operator";
import { RolesPage } from "./pages/Roles";
import { TriagePage } from "./pages/Triage";
import { Products } from "./pages/Products";
import { PricesPage } from "./pages/Prices";
import { QuotePage } from "./pages/QuotePage";
import { Requirements } from "./pages/Requirements";
import { Search } from "./pages/Search";
import { Suppliers } from "./pages/Suppliers";
import { Outbox } from "./pages/Outbox";
import { Soon } from "./pages/Soon";
import { SystemHome } from "./pages/SystemHome";
import { Workbench } from "./pages/Workbench";
import { ToastProvider } from "./ui/Toast";

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false, retry: 1 } } });

export default function App() {
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <BrowserRouter basename="/app">
          <Routes>
            <Route element={<AppShell />}>
              <Route index element={<Overview />} />
              <Route path="workbench" element={<Workbench />} />
              <Route path="overview" element={<Overview />} />
              <Route path="system/legacy" element={<LegacyPage />} />
              <Route path="guide" element={<Guide />} />
              <Route path="fulfilment" element={<FulfilBoardPage />} />
              <Route path="prices" element={<PricesPage />} />
              <Route path="audit" element={<AuditPage />} />
              <Route path="directory" element={<DirectoryPage />} />
              <Route path="classify" element={<ClassifyPage />} />
              <Route path="triage" element={<TriagePage />} />
              <Route path="roles" element={<RolesPage />} />
              <Route path="operator" element={<Operator />} />
              <Route path="deals" element={<Deals />} />
              <Route path="deals/:id" element={<DealPage />} />
              <Route path="quotes/:id" element={<QuotePage />} />
              <Route path="inbox" element={<Inbox />} />
              <Route path="customers" element={<Customers />} />
              <Route path="customers/:id" element={<Customers />} />
              <Route path="outbox" element={<Outbox />} />
              <Route path="system/channels" element={<Channels />} />
              <Route path="system/alibaba-open" element={<AlibabaOpen />} />
              <Route path="system/pipeline" element={<Pipeline />} />
              <Route path="system/language" element={<LanguageSettings />} />
              <Route path="packing" element={<Packing />} />
              <Route path="inspiration" element={<Inspiration />} />
              <Route path="requirements" element={<Requirements />} />
              <Route path="search" element={<Search />} />
              <Route path="supplier-dossiers" element={<SupplierDossiers />} />
              <Route path="supplier-dossiers/:id" element={<SupplierDossiers />} />
              <Route path="product-dossiers" element={<ProductDossiers />} />
              <Route path="product-dossiers/:id" element={<ProductDossiers />} />
              <Route path="dossier-gaps" element={<DossierGaps />} />
              <Route path="suppliers" element={<Suppliers />} />
              <Route path="suppliers/:id" element={<Suppliers />} />
              <Route path="products" element={<Products />} />
              <Route path="freight" element={<Freight />} />
              <Route path="system/knowledge" element={<KnowledgeHub />} />
              <Route path="system" element={<SystemHome />} />
              {PLANNED_PAGES.map((item) => (
                <Route key={item.key} path={item.path.slice(1)} element={<Soon />} />
              ))}
              <Route path="*" element={<Soon />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}
