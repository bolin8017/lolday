/**
 * Shape of `formContext` passed from RjsfConfigForm to its widgets / templates.
 * Importers on both ends (writer in RjsfConfigForm.tsx, reader in
 * templates/FieldTemplate.tsx) reference this type so a typo on either
 * side becomes a compile error rather than a silent contract break.
 */
export interface RjsfFormContext {
  onResetField?: (fieldId: string) => void;
}
