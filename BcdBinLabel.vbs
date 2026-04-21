'	b-PAC 3.0 SDK Component Sample (Print Barcode Label)
'	(C)Copyright Brother Industries, Ltd. 2009
'
'<SCRIPT LANGUAGE="VBScript">

	' Get Item's name & code from arguments list
	set Args = Wscript.Arguments
	If Args.count < 3 Then
		wscript.quit(1)
	End If
    sBinType = Args(1)
    sBinNumber = Args(2)

	' Data Folder
	sDataFolder =  UCase(Left(Wscript.ScriptFullName, Len(Wscript.ScriptFullName) - Len(Wscript.ScriptName) - 1))
	
	' Print
	DoPrint(sDataFolder & "\" & sBinType & ".lbx")


	'*******************************************************************
	'	Print Module
	'*******************************************************************
    Function GetNamedObject(doc, names)
        Dim i, obj
        Set GetNamedObject = Nothing
        For i = 0 To UBound(names)
            On Error Resume Next
            Set obj = doc.GetObject(names(i))
            If Err.Number = 0 Then
                If Not (obj Is Nothing) Then
                    Set GetNamedObject = obj
                    Exit Function
                End If
            End If
            Err.Clear
            On Error GoTo 0
        Next
    End Function

    Sub DoPrint(strFilePath)
        Dim binNumberObj
		Set ObjDoc = CreateObject("bpac.Document")
		bRet = ObjDoc.Open(strFilePath)
		If (bRet <> False) Then
            Set binNumberObj = GetNamedObject(ObjDoc, Array("BinNumber"))

            If binNumberObj Is Nothing Then
                MsgBox "Could not find an object named 'BinNumber' in " & sBinType & ".lbx.", vbCritical, "Bin Label Template Error"
                ObjDoc.Close
                Set ObjDoc = Nothing
                Exit Sub
            End If

            binNumberObj.Text = sBinNumber
			' ObjDoc.SetMediaByName ObjDoc.Printer.GetMediaName(), True
			ObjDoc.StartPrint "", 0
			ObjDoc.PrintOut 1, 0
			ObjDoc.EndPrint
			ObjDoc.Close
        Else
            MsgBox "Could not open label template: " & strFilePath, vbCritical, "Bin Label Template Error"
		End If
		Set ObjDoc = Nothing
	End Sub
