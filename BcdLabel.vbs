'	b-PAC 3.0 SDK Component Sample (Print Barcode Label)
'	(C)Copyright Brother Industries, Ltd. 2009
'
'<SCRIPT LANGUAGE="VBScript">

	' Get Item's name & code from arguments list
	set Args = Wscript.Arguments
	If Args.count < 1 Then
		wscript.quit(1)
	End If
	sCode = Args(0)

	' Data Folder
	sDataFolder =  UCase(Left(Wscript.ScriptFullName, Len(Wscript.ScriptFullName) - Len(Wscript.ScriptName) - 1))
	
	' Print
	DoPrint(sDataFolder & "\128code_8.lbx")


	'*******************************************************************
	'	Print Module
	'*******************************************************************
    Sub DoPrint(strFilePath)
		Set ObjDoc = CreateObject("bpac.Document")
		bRet = ObjDoc.Open(strFilePath)
		If (bRet <> False) Then
			ObjDoc.GetObject("barcode").Text = sCode
			' ObjDoc.SetMediaByName ObjDoc.Printer.GetMediaName(), True
			ObjDoc.StartPrint "", 0
			ObjDoc.PrintOut 1, 0
			ObjDoc.EndPrint
			ObjDoc.Close
		End If
		Set ObjDoc = Nothing
	End Sub