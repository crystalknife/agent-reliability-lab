/**
 * Vendored + adapted from Animate UI `collapsible.tsx`
 * (https://github.com/imskyleen/animate-ui, MIT + Commons Clause — see LICENSE note).
 * Verified source 2026-10-06: Radix Collapsible primitive animated with
 * motion/react height animation.
 * Adaptation: imports @radix-ui/react-collapsible directly instead of the
 * `radix-ui` meta-package (only primitive we need); className API preserved.
 */
import * as React from "react";
import {
  Collapsible as CollapsibleRoot,
  CollapsibleContent as RadixContent,
  CollapsibleTrigger as RadixTrigger,
} from "@radix-ui/react-collapsible";
import { AnimatePresence, motion } from "motion/react";

type CollapsibleContextType = { isOpen: boolean };

const CollapsibleContext = React.createContext<CollapsibleContextType | undefined>(undefined);

export function useCollapsible(): CollapsibleContextType {
  const context = React.useContext(CollapsibleContext);
  if (!context) throw new Error("useCollapsible must be used within a Collapsible");
  return context;
}

type CollapsibleProps = React.ComponentProps<typeof CollapsibleRoot>;

export function Collapsible({ children, ...props }: CollapsibleProps) {
  const [isOpen, setIsOpen] = React.useState(props?.open ?? props?.defaultOpen ?? false);

  React.useEffect(() => {
    if (props?.open !== undefined) setIsOpen(props.open);
  }, [props?.open]);

  const handleOpenChange = React.useCallback(
    (open: boolean) => {
      setIsOpen(open);
      props.onOpenChange?.(open);
    },
    [props],
  );

  return (
    <CollapsibleContext.Provider value={{ isOpen }}>
      <CollapsibleRoot onOpenChange={handleOpenChange} {...props} open={isOpen}>
        {children}
      </CollapsibleRoot>
    </CollapsibleContext.Provider>
  );
}

export function CollapsibleTrigger(props: React.ComponentProps<typeof RadixTrigger>) {
  return <RadixTrigger {...props} />;
}

export function CollapsibleContent({
  children,
  ...props
}: React.ComponentProps<typeof RadixContent>) {
  const { isOpen } = useCollapsible();
  return (
    <AnimatePresence initial={false}>
      {isOpen && (
        <RadixContent forceMount {...props} asChild>
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22, ease: [0.32, 0.72, 0, 1] }}
            style={{ overflow: "hidden" }}
          >
            {children}
          </motion.div>
        </RadixContent>
      )}
    </AnimatePresence>
  );
}
