"use client";

import { useEffect } from "react";
import { useQuery } from "@/lib/api";

/** Old "Why?" links now open the Flow view. */
export default function Trail() {
  const id = useQuery("id");
  useEffect(() => {
    window.location.replace(id ? `/flow?id=${id}` : "/flow");
  }, [id]);
  return null;
}
