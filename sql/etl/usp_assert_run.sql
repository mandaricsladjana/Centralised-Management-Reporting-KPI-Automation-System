/* =============================================================================
   etl.usp_assert_run: guard used by every load procedure.
   A dw row may only be written under a pipeline run that exists and is still
   'Running', so lineage (etl_run_id) always points at a real, open run.
   ============================================================================= */
CREATE OR ALTER PROCEDURE etl.usp_assert_run
    @etl_run_id INT
AS
BEGIN
    SET NOCOUNT ON;
    IF NOT EXISTS (SELECT 1 FROM [audit].etl_run WHERE etl_run_id = @etl_run_id AND status = 'Running')
    BEGIN
        DECLARE @msg NVARCHAR(200) = CONCAT(N'etl_run_id ', COALESCE(CAST(@etl_run_id AS NVARCHAR(12)), N'NULL'),
                                            N' does not exist or is not Running.');
        THROW 50000, @msg, 1;
    END;
END;
