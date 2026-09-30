"use client";

import { useEffect } from "react";

export function useTitle(title: string | null | undefined): void {
  useEffect(() => {
    if (title) document.title = `${title} · BYOC Console`;
  }, [title]);
}
