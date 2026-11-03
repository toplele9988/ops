import { DealOffers } from "./DealOffers";
import { DemandCandidates } from "./DemandCandidates";
import { WaitingPanel } from "./WaitingPanel";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

export function DealWorkflow({ dealId, revision }: { dealId: number; revision: string }) {
  const client = useQueryClient();
  useEffect(() => {
    for (const name of ["deal-demands", "deal-offers", "deal-waiting"]) client.invalidateQueries({ queryKey: [name, dealId] });
  }, [client, dealId, revision]);
  return <><DemandCandidates dealId={dealId} /><DealOffers dealId={dealId} /><WaitingPanel dealId={dealId} /></>;
}
